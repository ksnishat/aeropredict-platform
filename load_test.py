"""Concurrent load test for the four portfolio APIs.

Measures p50/p95/p99 latency and throughput at a fixed concurrency. Uses only
the standard library plus requests, so there is no new tooling to install.

Usage: python loadtest.py [--requests N] [--concurrency C]
"""

import argparse
import json
import statistics
import threading
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
# When run from inside a project repo the datasets live under that repo;
# when run from the workspace root they live under the project directories.
WORKSPACE = BASE if (BASE / "aeropredict-platform").is_dir() else BASE.parent
KEY = "demo-secret-key"

CMAPSS = WORKSPACE / "aeropredict-platform/Aircraft-Engine-Failure-Prediction-using-Deep-Learning-main/CMAPSSData/test_FD001.txt"
RUST = next((WORKSPACE / "infralens/Rust Detection.v1i.yolov8/test/images").glob("*.jpg"), None)
EGG = next((WORKSPACE / "ovoscan-pipeline/egg_dataset/valid/images").glob("*.jpg"), None)

ROSSMANN_BODY = json.dumps({
    "Store": 1, "DayOfWeek": 3, "Date": "2015-07-29", "Open": 1, "Promo": 1,
    "StateHoliday": "0", "SchoolHoliday": 0, "StoreType": "c", "Assortment": "a",
    "CompetitionDistance": 1270.0, "CompetitionOpenSinceMonth": 9,
    "CompetitionOpenSinceYear": 2008, "Promo2": 0, "Promo2SinceWeek": 1,
    "Promo2SinceYear": 2010, "PromoInterval": "",
}).encode()


def _window():
    rows = [ln for ln in CMAPSS.read_text().splitlines() if ln.split()[0] == "50"]
    return "\n".join(rows[-50:]).encode()


def _multipart(field, filename, data):
    b = b"--BOUNDARY\r\n"
    b += f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
    b += b"Content-Type: application/octet-stream\r\n\r\n" + data + b"\r\n--BOUNDARY--\r\n"
    return b


def make_request(name):
    """Return (url, data, headers) for one call to the named API."""
    h = {"X-API-Key": KEY}
    if name == "aeropredict":
        body = _multipart("file", "w.txt", _window())
        h["Content-Type"] = "multipart/form-data; boundary=BOUNDARY"
        return "http://localhost:8000/predict", body, h
    if name == "infralens":
        body = _multipart("file", "r.jpg", RUST.read_bytes())
        h["Content-Type"] = "multipart/form-data; boundary=BOUNDARY"
        return "http://localhost:8001/predict", body, h
    if name == "rossmann":
        h["Content-Type"] = "application/json"
        return "http://localhost:8002/predict", ROSSMANN_BODY, h
    if name == "ovoscan":
        body = _multipart("file", "e.jpg", EGG.read_bytes())
        h["Content-Type"] = "multipart/form-data; boundary=BOUNDARY"
        return "http://localhost:8003/predict", body, h
    raise ValueError(name)


def run(name, total, concurrency):
    url, body, headers = make_request(name)
    latencies, errors = [], []
    lock = threading.Lock()
    per_thread = total // concurrency

    def worker():
        local = []
        errs = 0
        for _ in range(per_thread):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    r.read()
                    if r.status != 200:
                        errs += 1
            except Exception:
                errs += 1
            local.append((time.perf_counter() - t0) * 1000)
        with lock:
            latencies.extend(local)
            errors.append(errs)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    n = len(latencies)
    latencies.sort()

    def pct(p):
        return latencies[min(int(len(latencies) * p), len(latencies) - 1)]

    return {
        "name": name,
        "requests": n,
        "errors": sum(errors),
        "wall_s": round(wall, 2),
        "rps": round(n / wall, 1) if wall else 0,
        "p50_ms": round(pct(0.50), 1),
        "p95_ms": round(pct(0.95), 1),
        "p99_ms": round(pct(0.99), 1),
        "mean_ms": round(statistics.mean(latencies), 1),
        "max_ms": round(max(latencies), 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()

    print(f"requests={args.requests} concurrency={args.concurrency}\n")
    results = []
    for name in ["aeropredict", "infralens", "rossmann", "ovoscan"]:
        r = run(name, args.requests, args.concurrency)
        results.append(r)
        print(f"{r['name']:14s} {r['requests']:4d} req  {r['rps']:6.1f} req/s  "
              f"p50 {r['p50_ms']:7.1f}  p95 {r['p95_ms']:7.1f}  p99 {r['p99_ms']:7.1f}  "
              f"max {r['max_ms']:7.1f} ms  errors {r['errors']}")

    out = WORKSPACE / "job_preparation" / "load_test_results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
