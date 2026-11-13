"""Tests for the streaming feature pipeline.

The most important test in this file is `TestBatchParity`. If the streaming
path computes different features from the batch path for the same input, the
model receives out-of-distribution data and the failure is silent -- the same
class of bug as the original train/serve skew, moved to a new place.

Everything here runs without Kafka or Redis. The windowing logic is separated
from the broker on purpose so it can be tested deterministically.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.streaming import (
    EngineWindow,
    WindowBuilder,
    build_window,
    iter_telemetry,
    parse_telemetry,
    read_window,
    window_key,
    write_window,
)

SENSORS = [f"s{i}" for i in range(1, 22)]


def make_payload(engine="1", cycle=1, value=1.0):
    p = {"engine_id": engine, "cycle": cycle}
    for c in SENSORS:
        p[c] = value
    return p


class FakeRedis:
    """Minimal stand-in for redis.Redis: get/setex only."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

    def get(self, key):
        v = self.store.get(key)
        return v.encode() if isinstance(v, str) else v


class TestParseTelemetry:
    def test_accepts_a_well_formed_message(self):
        engine, cycle, values = parse_telemetry(make_payload("7", 42, 3.5))
        assert engine == "7"
        assert cycle == 42
        assert len(values) == 21
        assert values[0] == 3.5

    def test_accepts_engine_as_an_alias(self):
        p = make_payload()
        p["engine"] = p.pop("engine_id")
        engine, _, _ = parse_telemetry(p)
        assert engine == "1"

    def test_rejects_a_message_with_no_engine(self):
        p = make_payload()
        del p["engine_id"]
        with pytest.raises(ValueError, match="no engine_id"):
            parse_telemetry(p)

    def test_rejects_a_message_with_no_cycle(self):
        p = make_payload()
        del p["cycle"]
        with pytest.raises(ValueError, match="no cycle"):
            parse_telemetry(p)

    def test_rejects_missing_sensor_channels(self):
        """A partial row would become a corrupt window, not a visible error."""
        p = make_payload()
        del p["s7"]
        with pytest.raises(ValueError, match="missing sensor channels"):
            parse_telemetry(p)

    def test_rejects_non_numeric_sensor_values(self):
        p = make_payload()
        p["s3"] = "hot"
        with pytest.raises(ValueError, match="non-numeric"):
            parse_telemetry(p)


class TestBuildWindow:
    def test_trims_to_the_last_window_rows(self):
        rows = [[float(i)] for i in range(60)]
        out = build_window(rows, window=50)
        assert len(out) == 50
        assert out[-1] == [59.0]
        assert out[0] == [10.0]

    def test_pads_a_short_history_by_repeating_the_first_row(self):
        """Matches preprocess_for_inference, which pads the same way."""
        rows = [[1.0], [2.0], [3.0]]
        out = build_window(rows, window=5)
        assert len(out) == 5
        assert out[:2] == [[1.0], [1.0]]
        assert out[-1] == [3.0]

    def test_rejects_an_empty_history(self):
        with pytest.raises(ValueError, match="zero rows"):
            build_window([], window=5)

    def test_returns_a_copy_not_the_input(self):
        rows = [[1.0]]
        out = build_window(rows, window=1)
        out[0][0] = 99.0
        assert rows[0][0] == 1.0


class TestEngineWindow:
    def test_keeps_only_the_most_recent_rows(self):
        w = EngineWindow("1")
        for i in range(60):
            w.add(i, [float(i)], window=50)
        assert len(w.rows) == 50
        assert w.last_cycle == 59

    def test_not_ready_until_the_window_is_full(self):
        w = EngineWindow("1")
        for i in range(49):
            w.add(i, [float(i)])
        assert not w.ready
        w.add(49, [49.0])
        assert w.ready


class TestWindowBuilder:
    def test_writes_a_window_to_redis(self):
        client = FakeRedis()
        b = WindowBuilder(client)
        assert b.handle(make_payload("1", 1, 2.0))
        stored = read_window(client, "1")
        assert stored is not None
        assert stored["last_cycle"] == 1
        assert len(stored["rows"]) == 1

    def test_sets_a_ttl_so_a_dead_producer_stops_serving(self):
        """A stale window is worse than no window."""
        client = FakeRedis()
        b = WindowBuilder(client, ttl=123)
        b.handle(make_payload("1", 1))
        assert client.ttls[window_key("1")] == 123

    def test_rejects_a_bad_message_without_raising(self):
        client = FakeRedis()
        b = WindowBuilder(client)
        assert b.handle({"engine_id": "1"}) is False
        assert b.rejected == 1
        assert b.last_error

    def test_tracks_engines_separately(self):
        client = FakeRedis()
        b = WindowBuilder(client)
        b.handle(make_payload("1", 1, 1.0))
        b.handle(make_payload("2", 1, 9.0))
        assert read_window(client, "1")["rows"][0][0] == 1.0
        assert read_window(client, "2")["rows"][0][0] == 9.0

    def test_stats_reports_ready_engines(self):
        client = FakeRedis()
        b = WindowBuilder(client, window=3)
        for i in range(3):
            b.handle(make_payload("1", i))
        stats = b.stats()
        assert stats["engines"] == 1
        assert stats["accepted"] == 3
        assert stats["ready"] == ["1"]


class TestReadWindow:
    def test_returns_none_for_an_unknown_engine(self):
        assert read_window(FakeRedis(), "nope") is None

    def test_returns_none_for_corrupt_json(self):
        """A corrupt cache entry must look absent, not crash the endpoint."""
        client = FakeRedis()
        client.store[window_key("1")] = "{not json"
        assert read_window(client, "1") is None


class TestBatchParity:
    """The streaming path must produce the same features as the batch path.

    This is the whole point of the module. A divergence here is silent: the
    model still returns a number, it is just wrong.
    """

    def _batch_window(self, rows: list[list[float]], window: int = 50):
        """Run the batch path's windowing on the same rows."""
        from src.data_preprocessing import preprocess_for_inference

        class IdentityScaler:
            def transform(self, values):
                return values

        df = pd.DataFrame(rows, columns=SENSORS)
        tensor = preprocess_for_inference(df, IdentityScaler(), window_size=window)
        return tensor.squeeze(0).numpy()

    def test_full_window_matches_the_batch_path(self):
        rng = np.random.default_rng(0)
        rows = rng.random((50, 21)).astype(np.float32).tolist()

        streamed = np.array(build_window(rows, window=50), dtype=np.float32)
        batched = self._batch_window(rows, window=50)

        np.testing.assert_allclose(streamed, batched, rtol=1e-6, atol=1e-6)

    def test_short_history_padding_matches_the_batch_path(self):
        """Both paths pad by repeating the first row; they must agree."""
        rng = np.random.default_rng(1)
        rows = rng.random((10, 21)).astype(np.float32).tolist()

        streamed = np.array(build_window(rows, window=50), dtype=np.float32)
        batched = self._batch_window(rows, window=50)

        np.testing.assert_allclose(streamed, batched, rtol=1e-6, atol=1e-6)

    def test_long_history_truncation_matches_the_batch_path(self):
        rng = np.random.default_rng(2)
        rows = rng.random((80, 21)).astype(np.float32).tolist()

        streamed = np.array(build_window(rows, window=50), dtype=np.float32)
        batched = self._batch_window(rows, window=50)

        np.testing.assert_allclose(streamed, batched, rtol=1e-6, atol=1e-6)


class TestIterTelemetry:
    def test_yields_payloads_from_a_cmapss_file(self, tmp_path):
        f = tmp_path / "test_FD001.txt"
        row = "1 1 -0.0007 -0.0004 100.0 " + " ".join(["518.67"] * 21)
        f.write_text(row + "\n" + row.replace("1 1 ", "1 2 ", 1) + "\n")

        payloads = list(iter_telemetry(str(f)))
        assert len(payloads) == 2
        assert payloads[0]["engine_id"] == "1"
        assert payloads[0]["cycle"] == 1
        assert payloads[1]["cycle"] == 2
        assert payloads[0]["s1"] == 518.67

    def test_filters_by_engine(self, tmp_path):
        f = tmp_path / "test.txt"
        base = " {cyc} -0.0007 -0.0004 100.0 " + " ".join(["518.67"] * 21)
        f.write_text(
            "1" + base.format(cyc=1) + "\n" + "2" + base.format(cyc=1) + "\n"
        )
        payloads = list(iter_telemetry(str(f), engine_id="2"))
        assert len(payloads) == 1
        assert payloads[0]["engine_id"] == "2"

    def test_skips_short_lines(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("1 1 2 3\n")
        assert list(iter_telemetry(str(f))) == []


class TestWindowKey:
    def test_key_is_namespaced_per_engine(self):
        assert window_key("42") == "engine:42:window"

    def test_write_then_read_round_trips(self):
        client = FakeRedis()
        rows = [[1.0] * 21, [2.0] * 21]
        write_window(client, "9", rows, last_cycle=7, ttl=60)
        got = read_window(client, "9")
        assert got["rows"] == rows
        assert got["last_cycle"] == 7
        assert json.dumps(got)  # serialisable


class TestEndpointParity:
    """The live endpoint must return the same RUL as the batch endpoint.

    Verified manually against the running stack: engine 1, cycles 11-60 gave
    RUL 123 from both POST /predict and GET /predict/live/1. This test pins the
    property that makes that true -- the window the endpoint builds is the same
    window the batch path would build from the same rows.
    """

    def test_endpoint_window_equals_batch_window(self):
        """build_window output fed to the batch transform must match directly."""
        from src.data_preprocessing import preprocess_for_inference

        rng = np.random.default_rng(7)
        rows = rng.random((50, 21)).astype(np.float32).tolist()

        class IdentityScaler:
            def transform(self, values):
                return values

        # Path A: what the live endpoint does -- build_window then DataFrame.
        streamed = pd.DataFrame(
            build_window(rows, window=50), columns=SENSORS
        )
        a = preprocess_for_inference(streamed, IdentityScaler(), window_size=50)

        # Path B: what the batch endpoint does -- raw rows straight in.
        batched = pd.DataFrame(rows, columns=SENSORS)
        b = preprocess_for_inference(batched, IdentityScaler(), window_size=50)

        np.testing.assert_allclose(
            a.squeeze(0).numpy(), b.squeeze(0).numpy(), rtol=1e-6, atol=1e-6
        )


class TestConsumerOrdering:
    """The consumer must be running before the producer publishes.

    `consume_forever` uses auto_offset_reset="latest", so messages published
    before the consumer joins the group are skipped. Verified locally: starting
    the producer first produced 60 messages and left the feature store empty.
    This test pins the setting that causes it, so a change to "earliest" is a
    deliberate decision rather than an accident.
    """

    def test_consumer_defaults_to_latest_offset(self):
        import inspect

        from src.streaming import consume_forever

        src = inspect.getsource(consume_forever)
        assert 'auto_offset_reset="latest"' in src
