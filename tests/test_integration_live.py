"""Integration tests against the live docker stack.

These are skipped unless ``LIVE_STACK=1`` is set, because they require the
services started by ``start_all_stacks.sh`` (API, MLflow, Prometheus, Grafana,
MinIO) to be reachable on localhost. Everything else in this suite uses
``TestClient``, which exercises the ASGI app in-process and therefore cannot
catch wiring problems such as a wrong port, a container that never came up, or
Prometheus failing to scrape its target.

Run with::

    LIVE_STACK=1 pytest tests/test_integration_live.py -v
"""

import os

import pytest
import requests

pytestmark = pytest.mark.integration

API_PORT = int(os.getenv("AEROPREDICT_API_PORT", "8000"))
MLFLOW_PORT = int(os.getenv("AEROPREDICT_MLFLOW_PORT", "5001"))
PROM_PORT = int(os.getenv("AEROPREDICT_PROM_PORT", "9090"))
GRAFANA_PORT = int(os.getenv("AEROPREDICT_GRAFANA_PORT", "3001"))
MINIO_PORT = int(os.getenv("AEROPREDICT_MINIO_PORT", "9000"))

API = f"http://127.0.0.1:{API_PORT}"
TIMEOUT = 30


def _require_live() -> None:
    if os.getenv("LIVE_STACK") != "1":
        pytest.skip("set LIVE_STACK=1 to run integration tests against the live stack")


# The APIs enforce X-API-Key when API_KEY is set. The live stack runs with auth
# enabled, so every request must carry the key. Set LIVE_API_KEY to match the
# stack; the default matches run_stack.sh's demo value.
API_KEY = os.getenv("LIVE_API_KEY", "demo-secret-key")
AUTH_HEADERS = {"X-API-Key": API_KEY}


def _auth(kw):
    """Merge the API key into a request's headers."""
    headers = dict(kw.pop("headers", {}) or {})
    headers.update(AUTH_HEADERS)
    kw["headers"] = headers
    return kw


def _get(url: str, **kw):
    try:
        return requests.get(url, timeout=TIMEOUT, **_auth(kw))
    except requests.RequestException as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"service unreachable: {exc}")


def test_api_health_endpoint_is_live():
    _require_live()
    r = _get(f"{API}/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["model_loaded"] is True, "API started without a model checkpoint"


def test_api_metrics_are_scrapable():
    """Prometheus needs a text exposition endpoint, not JSON."""
    _require_live()
    r = _get(f"{API}/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    assert "aeropredict_model_loaded" in r.text


def test_predict_returns_a_real_prediction():
    """A 50-cycle window from the FD001 test set must produce a plausible RUL."""
    _require_live()
    data_dir = os.getenv(
        "CMAPSS_DATA_DIR",
        "Aircraft-Engine-Failure-Prediction-using-Deep-Learning-main/CMAPSSData",
    )
    test_file = os.path.join(data_dir, "test_FD001.txt")
    if not os.path.exists(test_file):
        pytest.skip(f"CMAPSS test data not found at {test_file}")

    import numpy as np

    raw = np.loadtxt(test_file)
    engine_100 = raw[raw[:, 0] == 100][-50:]
    # The API parses uploads with sep=r"\s+", matching the raw C-MAPSS format.
    csv = "\n".join(" ".join(f"{v:g}" for v in row) for row in engine_100)

    r = requests.post(f"{API}/predict", files={"file": ("engine100.csv", csv, "text/csv")},
        timeout=TIMEOUT,
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Engine 100's published RUL is 20 cycles. Allow a generous band: this
    # asserts the model is doing real inference, not that it is exact.
    assert 0 <= body["rul"] <= 60, body
    assert body["risk_level"] in {"LOW", "WARNING", "CRITICAL"}


def test_predict_rejects_non_sensor_payload():
    _require_live()
    r = requests.post(f"{API}/predict", files={"file": ("junk.csv", "hello,world\nfoo,bar\n", "text/csv")},
        timeout=TIMEOUT,
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422, r.text


def test_mlflow_tracking_server_is_reachable():
    _require_live()
    # MLflow validates the Host header; a bare 127.0.0.1 is not in its default
    # allow-list, so send an explicit Host.
    r = _get(
        f"http://127.0.0.1:{MLFLOW_PORT}/api/2.0/mlflow/experiments/search",
        headers={"Host": f"127.0.0.1:{MLFLOW_PORT}"},
        params={"max_results": 1},
    )
    assert r.status_code == 200, r.text
    assert "experiments" in r.json()


def test_prometheus_is_scraping_the_api():
    """A healthy Prometheus is not enough: the target must actually be up."""
    _require_live()
    r = _get(f"http://127.0.0.1:{PROM_PORT}/api/v1/targets")
    assert r.status_code == 200
    targets = r.json()["data"]["activeTargets"]
    assert targets, "Prometheus has no configured targets"
    for t in targets:
        assert t["health"] == "up", f"{t['scrapeUrl']} is {t['health']}: {t.get('lastError')}"


def test_grafana_is_healthy():
    _require_live()
    r = _get(f"http://127.0.0.1:{GRAFANA_PORT}/api/health")
    assert r.status_code == 200
    assert r.json()["database"] == "ok"


def test_minio_is_live():
    _require_live()
    r = _get(f"http://127.0.0.1:{MINIO_PORT}/minio/health/live")
    assert r.status_code == 200
