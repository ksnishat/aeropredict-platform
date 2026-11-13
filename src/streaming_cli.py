"""Command-line entry points for the streaming pipeline.

    python -m src.streaming_cli produce --engine 1 --limit 60
    python -m src.streaming_cli consume --max-messages 60
    python -m src.streaming_cli stats

Kept separate from `streaming.py` so the library has no argparse dependency and
the tests can import it without side effects.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The module is run both as `python -m src.streaming_cli` (package context) and
# as `python src/streaming_cli.py` (flat). `streaming` is importable only in the
# second, so put src/ on the path explicitly rather than relying on the caller.
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_DATA = os.getenv(
    "CMAPSS_TEST_FILE",
    "Aircraft-Engine-Failure-Prediction-using-Deep-Learning-main/CMAPSSData/train_FD001.txt",
)


def _redis():
    import redis  # noqa: PLC0415

    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_prod = sub.add_parser("produce", help="Replay C-MAPSS rows to Kafka")
    p_prod.add_argument("--file", default=DEFAULT_DATA)
    p_prod.add_argument("--engine", default=None)
    p_prod.add_argument("--rate", type=float, default=10.0, help="messages/second")
    p_prod.add_argument("--limit", type=int, default=None)

    p_cons = sub.add_parser("consume", help="Build windows from Kafka into Redis")
    p_cons.add_argument("--max-messages", type=int, default=None)
    p_cons.add_argument("--group", default="aeropredict-window-builder")

    sub.add_parser("stats", help="Show what is currently in the feature store")

    args = parser.parse_args(argv)

    if args.command == "produce":
        from streaming import replay_dataset  # noqa: PLC0415

        sent = replay_dataset(
            args.file,
            engine_id=args.engine,
            rate_hz=args.rate,
            limit=args.limit,
        )
        print(f"produced {sent} messages")
        return 0

    if args.command == "consume":
        from streaming import WindowBuilder, consume_forever  # noqa: PLC0415

        builder = WindowBuilder(_redis())
        try:
            consume_forever(
                builder,
                group_id=args.group,
                max_messages=args.max_messages,
            )
        except KeyboardInterrupt:
            pass
        print(json.dumps(builder.stats(), indent=2))
        return 0

    if args.command == "stats":
        from streaming import read_window  # noqa: PLC0415

        client = _redis()
        keys = list(client.scan_iter("engine:*:window"))
        out = []
        for key in keys:
            engine = key.decode().split(":")[1]
            w = read_window(client, engine)
            if w:
                out.append(
                    {
                        "engine": engine,
                        "rows": len(w["rows"]),
                        "last_cycle": w["last_cycle"],
                    }
                )
        print(json.dumps(out, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
