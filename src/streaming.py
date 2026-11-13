"""Streaming feature pipeline: Kafka telemetry -> Redis online feature store.

The batch path requires a client to upload 50 cycles of CSV before it can
predict. That is fine for a maintenance inspection and wrong for an operating
engine, which emits sensor readings continuously. This module adds the
streaming half:

    producer  -> Kafka topic `sensor-telemetry`
    consumer  -> rolling 50-cycle window per engine -> Redis
    API       -> GET /predict/live/{engine_id} reads Redis

The single most important property here is that the streaming path computes
**identical** features to the batch path for the same input. If it does not,
the model receives out-of-distribution data and the failure is silent -- the
same class of bug as the original train/serve skew, in a new place. That is why
`build_window` is shared and why `test_streaming.py` asserts equality against
`preprocess_for_inference` rather than against a hand-written expectation.

Design notes
------------
* **Redis holds the raw window, not the scaled tensor.** Scaling happens at
  read time with the same persisted scaler the batch path uses. Storing scaled
  values would freeze the scaler into the cache and make a scaler update
  silently inconsistent with already-cached windows.
* **Windows are per engine.** A window never spans two engines, matching the
  training-time rule.
* **TTL on every key.** A stale window is worse than no window: it would serve
  a prediction for an engine that stopped reporting an hour ago.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DEFAULT_TOPIC = "sensor-telemetry"
DEFAULT_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9094")
DEFAULT_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DEFAULT_WINDOW = 50
# A window older than this is not a live reading. 10 minutes is generous for a
# 10 Hz feed and short enough that a dead producer stops serving predictions.
DEFAULT_TTL_SECONDS = 600

SENSOR_COLUMNS = [f"s{i}" for i in range(1, 22)]


# ------------------------------------------------------------------ features


@dataclass
class EngineWindow:
    """The rolling state for one engine."""

    engine_id: str
    rows: list[list[float]] = field(default_factory=list)
    last_cycle: int = 0
    updated_at: float = 0.0

    def add(self, cycle: int, values: list[float], window: int = DEFAULT_WINDOW) -> None:
        """Append one cycle, keeping only the most recent `window` rows."""
        self.rows.append(values)
        if len(self.rows) > window:
            self.rows = self.rows[-window:]
        self.last_cycle = cycle
        self.updated_at = time.time()

    @property
    def ready(self) -> bool:
        """True when the window is full enough to predict from."""
        return len(self.rows) >= DEFAULT_WINDOW

    def is_ready(self, window: int) -> bool:
        """Readiness against a caller-supplied window size.

        The property uses the module default, which is wrong for any builder
        configured with a different window -- a bug the tests caught with a
        window of 3.
        """
        return len(self.rows) >= window


def parse_telemetry(payload: dict[str, Any]) -> tuple[str, int, list[float]]:
    """Validate one Kafka message into (engine_id, cycle, sensor values).

    Raises ValueError with a specific reason rather than returning a partial
    row: a malformed message that is silently accepted becomes a corrupt
    window, and a corrupt window produces a confident wrong prediction.
    """
    engine = payload.get("engine_id", payload.get("engine"))
    if engine is None:
        raise ValueError("message has no engine_id")
    cycle = payload.get("cycle")
    if cycle is None:
        raise ValueError("message has no cycle")

    missing = [c for c in SENSOR_COLUMNS if c not in payload]
    if missing:
        raise ValueError(f"message missing sensor channels: {missing[:5]}")

    try:
        values = [float(payload[c]) for c in SENSOR_COLUMNS]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric sensor value: {exc}") from exc

    return str(engine), int(cycle), values


def build_window(rows: list[list[float]], window: int = DEFAULT_WINDOW) -> list[list[float]]:
    """Pad or trim rows to exactly `window` cycles.

    Padding repeats the first reading, which is what `preprocess_for_inference`
    does for a short upload. Keeping the rule in one place is what makes the
    two paths agree.
    """
    if not rows:
        raise ValueError("cannot build a window from zero rows")
    if len(rows) < window:
        pad = [list(rows[0]) for _ in range(window - len(rows))]
        return pad + [list(r) for r in rows]
    return [list(r) for r in rows[-window:]]


# ------------------------------------------------------------------- redis io


def window_key(engine_id: str) -> str:
    return f"engine:{engine_id}:window"


def write_window(
    client,
    engine_id: str,
    rows: list[list[float]],
    last_cycle: int,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> None:
    """Persist a window to Redis with a TTL."""
    payload = json.dumps({"rows": rows, "last_cycle": last_cycle, "ts": time.time()})
    client.setex(window_key(engine_id), ttl, payload)


def read_window(client, engine_id: str) -> dict[str, Any] | None:
    """Read a window, or None when the engine has no live data."""
    raw = client.get(window_key(engine_id))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("corrupt window for engine %s; treating as absent", engine_id)
        return None


# ------------------------------------------------------------------- consumer


class WindowBuilder:
    """Accumulates telemetry into per-engine windows and writes them to Redis.

    Kept separate from the Kafka consumer loop so the windowing logic is
    testable without a broker.
    """

    def __init__(self, client, window: int = DEFAULT_WINDOW, ttl: int = DEFAULT_TTL_SECONDS):
        self.client = client
        self.window = window
        self.ttl = ttl
        self.engines: dict[str, EngineWindow] = {}
        self.accepted = 0
        self.rejected = 0
        self.last_error: str | None = None

    def handle(self, payload: dict[str, Any]) -> bool:
        """Process one message. Returns True when it was accepted."""
        try:
            engine_id, cycle, values = parse_telemetry(payload)
        except ValueError as exc:
            self.rejected += 1
            self.last_error = str(exc)
            logger.warning("rejected telemetry: %s", exc)
            return False

        win = self.engines.setdefault(engine_id, EngineWindow(engine_id))
        win.add(cycle, values, self.window)
        write_window(self.client, engine_id, win.rows, win.last_cycle, self.ttl)
        self.accepted += 1
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "engines": len(self.engines),
            "accepted": self.accepted,
            "rejected": self.rejected,
            "last_error": self.last_error,
            "ready": sorted(e for e, w in self.engines.items() if w.is_ready(self.window)),
        }


def consume_forever(
    builder: WindowBuilder,
    bootstrap: str = DEFAULT_BOOTSTRAP,
    topic: str = DEFAULT_TOPIC,
    group_id: str = "aeropredict-window-builder",
    max_messages: int | None = None,
) -> None:
    """Consume telemetry until interrupted (or `max_messages` is reached)."""
    from kafka import KafkaConsumer  # noqa: PLC0415

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        consumer_timeout_ms=1000,
    )
    logger.info("consuming %s from %s", topic, bootstrap)
    seen = 0
    try:
        while True:
            for message in consumer:
                builder.handle(message.value)
                seen += 1
                if max_messages is not None and seen >= max_messages:
                    return
    finally:
        consumer.close()


# ------------------------------------------------------------------- producer


def replay_dataset(
    path: str,
    engine_id: str | None = None,
    rate_hz: float = 10.0,
    limit: int | None = None,
    bootstrap: str = DEFAULT_BOOTSTRAP,
    topic: str = DEFAULT_TOPIC,
) -> int:
    """Replay C-MAPSS rows to Kafka at a fixed rate.

    Reads the raw file directly rather than through pandas so the producer has
    no dependency on the training stack. Column layout is the documented
    C-MAPSS one: id, cycle, setting1..3, s1..s21.
    """
    from kafka import KafkaProducer  # noqa: PLC0415

    producer = KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        linger_ms=5,
    )
    interval = 1.0 / rate_hz if rate_hz > 0 else 0.0
    sent = 0

    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 26:
                    continue
                row_engine = parts[0]
                if engine_id is not None and row_engine != str(engine_id):
                    continue
                payload = {
                    "engine_id": row_engine,
                    "cycle": int(float(parts[1])),
                }
                for i, col in enumerate(SENSOR_COLUMNS):
                    payload[col] = float(parts[5 + i])
                producer.send(topic, payload)
                sent += 1
                if limit is not None and sent >= limit:
                    break
                if interval:
                    time.sleep(interval)
        producer.flush()
    finally:
        producer.close()
    return sent


def iter_telemetry(path: str, engine_id: str | None = None) -> Iterable[dict[str, Any]]:
    """Yield telemetry payloads from a C-MAPSS file without a broker.

    Used by tests and by the consumer's offline mode, so the windowing logic can
    be exercised without Kafka running.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 26:
                continue
            if engine_id is not None and parts[0] != str(engine_id):
                continue
            payload: dict[str, Any] = {
                "engine_id": parts[0],
                "cycle": int(float(parts[1])),
            }
            for i, col in enumerate(SENSOR_COLUMNS):
                payload[col] = float(parts[5 + i])
            yield payload


__all__ = [
    "DEFAULT_TOPIC",
    "DEFAULT_WINDOW",
    "EngineWindow",
    "WindowBuilder",
    "build_window",
    "consume_forever",
    "iter_telemetry",
    "parse_telemetry",
    "read_window",
    "replay_dataset",
    "window_key",
    "write_window",
]
