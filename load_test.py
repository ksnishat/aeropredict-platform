"""Concurrent load test for this project's API.

Measures p50/p95/p99 latency and throughput at a fixed concurrency. Uses only
the standard library, so there is no new tooling to install.

The request body is built from data committed to this repository, so the script
works on a fresh clone with no datasets present. Where a real sample is
available it is preferred; otherwise a synthetic body of the right shape is
generated, which exercises the same code path in the API.

Usage:
    python load_test.py [--requests N] [--concurrency C]
    python load_test.py --url http://localhost:8000/predict
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
KEY = "demo-secret-key"

# Overridden per project by the generator below.
PROJECT = "aeropredict"
DEFAULT_URL = "http://localhost:8000/predict"
KIND = "multipart"          # json | multipart
SAMPLE_GLOBS = ["Aircraft-Engine-Failure-Prediction-using-Deep-Learning-main/CMAPSSData/test_FD001.txt"]


def _find_sample() -> Path | None:
    """Return a committed sample file, if this repository ships one."""
    for pattern in SAMPLE_GLOBS:
        for path in sorted(BASE.glob(pattern)):
            if path.is_file():
                return path
    return None


def _synthetic_body() -> bytes:
    """A body of the shape the API expects, built without any dataset."""
    # A C-MAPSS-shaped window: 50 rows of 26 numeric columns. The API reads the
    # last 21 columns as sensors, so the leading id/cycle/settings columns are
    # present but ignored. Values are mid-range for the FD001 sensors.
    rows = []
    for i in range(50):
        rows.append(" ".join(["1", str(i + 1), "0.0", "0.0", "100.0"] +
                             ["518.67", "642.0", "1585.0", "1400.0", "14.6",
                              "21.6", "554.0", "2388.0", "9050.0", "1.3",
                              "47.2", "521.7", "2388.0", "8125.0", "8.4",
                              "0.03", "392.0", "2388.0", "100.0", "38.9", "23.4"]))
    return "\n".join(rows).encode()


def _multipart(field: str, filename: str, data: bytes) -> bytes:
    b = b"--BOUNDARY\r\n"
    b += f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
    b += b"Content-Type: application/octet-stream\r\n\r\n" + data + b"\r\n--BOUNDARY--\r\n"
    return b


def build_request(url: str) -> tuple[str, bytes | None, dict]:
    """Return (url, body, headers) for one call."""
    headers = {"X-API-Key": KEY}

    if KIND == "json":
        headers["Content-Type"] = "application/json"
        return url, _synthetic_body(), headers

    sample = _find_sample()
    if sample is not None:
        data = sample.read_bytes()
        filename = sample.name
    else:
        data = _synthetic_body()
        filename = "synthetic.bin"

    headers["Content-Type"] = "multipart/form-data; boundary=BOUNDARY"
    return url, _multipart("file", filename, data), headers


def one_call(url: str, body: bytes | None, headers: dict) -> tuple[float, int]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        code = exc.code
    except urllib.error.URLError:
        code = 0
    return (time.perf_counter() - start) * 1000.0, code


def run(url: str, total: int, concurrency: int) -> dict:
    body, headers = build_request(url)[1:]
    latencies: list[float] = []
    errors = 0
    lock = threading.Lock()

    def worker(n: int) -> None:
        nonlocal errors
        local: list[float] = []
        local_errors = 0
        for _ in range(n):
            ms, code = one_call(url, body, headers)
            local.append(ms)
            if code != 200:
                local_errors += 1
        with lock:
            latencies.extend(local)
            errors += local_errors

    per_thread = max(1, total // concurrency)
    threads = [threading.Thread(target=worker, args=(per_thread,)) for _ in range(concurrency)]

    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start

    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(round(p / 100.0 * len(latencies))) - 1)
        return latencies[max(0, idx)]

    return {
        "project": PROJECT,
        "url": url,
        "requests": len(latencies),
        "concurrency": concurrency,
        "elapsed_s": round(elapsed, 3),
        "rps": round(len(latencies) / elapsed, 2) if elapsed else 0.0,
        "p50_ms": round(pct(50), 1),
        "p95_ms": round(pct(95), 1),
        "p99_ms": round(pct(99), 1),
        "max_ms": round(max(latencies), 1) if latencies else 0.0,
        "mean_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
        "errors": errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Load test for {PROJECT}")
    ap.add_argument("--requests", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--json-out", default=None,
                    help="write the result as JSON to this path")
    args = ap.parse_args()

    print(f"{PROJECT}: {args.requests} requests, concurrency {args.concurrency}")
    print(f"target: {args.url}\n")

    r = run(args.url, args.requests, args.concurrency)

    print(f"  requests   {r['requests']}")
    print(f"  throughput {r['rps']} req/s")
    print(f"  p50        {r['p50_ms']} ms")
    print(f"  p95        {r['p95_ms']} ms")
    print(f"  p99        {r['p99_ms']} ms")
    print(f"  max        {r['max_ms']} ms")
    print(f"  errors     {r['errors']}")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, indent=2) + "\n")
        print(f"\nwritten to {out}")

    return 1 if r["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
