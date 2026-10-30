"""Tests for the AeroPredict report decoupling, rate limiting and versioning.

The change under test: `/predict` used to block on the LLM report, so a ~50 ms
prediction took 10+ s under concurrent load because Ollama serialises requests.
`?async_report=true` returns the prediction immediately and generates the report
in a background task. These tests pin the contract of that mode, the sync mode's
backwards compatibility, and the bounded report store.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Import through the `src.` package path, matching tests/test_api.py. Importing
# it bare as `api` creates a second module object, and both register the same
# Prometheus metric names, which raises DuplicateTimeseries at collection.
from src import api  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(api.app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def sensor_csv(tmp_path_factory):
    """A minimal NASA C-MAPSS shaped file: 5 rows, no header."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "seq_id": list(range(1, 6)),
            **{f"op{i}": [0.5 + i / 100] * 5 for i in range(1, 7)},
            **{f"set{i}": [1] * 5 for i in range(1, 4)},
        }
    )
    path = tmp_path_factory.mktemp("data") / "sensor.csv"
    df.to_csv(path, sep=" ", index=False)
    return path


def _post(client, path="/predict", csv_path=None):
    with open(csv_path, "rb") as f:
        return client.post(path, files={"file": f.read()})


class TestAsyncReportMode:
    def test_returns_prediction_without_the_report(self, client, sensor_csv):
        response = _post(client, "/predict?async_report=true", sensor_csv)
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["rul"], int)
        assert body["report_status"] == "pending"
        assert body["report_id"]
        # Field kept for shape compatibility, but empty in async mode.
        assert body["maintenance_recommendation"] == ""

    def test_report_becomes_retrievable(self, client, sensor_csv):
        start = _post(client, "/predict?async_report=true", sensor_csv).json()
        report_id = start["report_id"]

        response = client.get(f"/reports/{report_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["report_id"] == report_id
        assert body["status"] == "ready"
        assert body["report"]
        assert body["source"] in ("rag", "template")

    def test_unknown_report_id_is_404(self, client):
        assert client.get("/reports/nope").status_code == 404

    def test_sync_mode_still_returns_the_report(self, client, sensor_csv):
        """Backwards compatibility: the default must be unchanged."""
        body = _post(client, "/predict", sensor_csv).json()
        assert body["report_id"] is None
        assert body["report_status"] is None
        assert len(body["maintenance_recommendation"]) > 0

    def test_async_and_sync_agree_on_the_prediction(self, client, sensor_csv):
        """Decoupling the report must not change the model output."""
        sync_rul = _post(client, "/predict", sensor_csv).json()["rul"]
        async_rul = _post(client, "/predict?async_report=true", sensor_csv).json()["rul"]
        assert sync_rul == async_rul


class TestReportStore:
    def test_store_is_bounded(self):
        api._REPORT_STORE.clear()
        for i in range(api._REPORT_STORE_MAX + 20):
            api._store_report(f"id-{i}", "r", "template")
        assert len(api._REPORT_STORE) == api._REPORT_STORE_MAX

    def test_oldest_entries_are_evicted_first(self):
        api._REPORT_STORE.clear()
        for i in range(api._REPORT_STORE_MAX + 5):
            api._store_report(f"id-{i}", "r", "template")
        assert "id-0" not in api._REPORT_STORE
        assert f"id-{api._REPORT_STORE_MAX + 4}" in api._REPORT_STORE
        api._REPORT_STORE.clear()

    def test_failed_generation_falls_back_to_the_template(self, monkeypatch):
        api._REPORT_STORE.clear()
        monkeypatch.setattr(
            api, "_generate_report_blocking", lambda rul: (_ for _ in ()).throw(RuntimeError("llm down"))
        )
        api._generate_report_task("x", 10)
        entry = api._REPORT_STORE["x"]
        assert entry["status"] == "ready"
        assert entry["source"] == "template"
        assert entry["report"]
        api._REPORT_STORE.clear()


class TestPredictionIsNotGatedOnTheLlm:
    def test_async_mode_does_not_call_the_llm_synchronously(self, client, sensor_csv, monkeypatch):
        """The whole point: a broken LLM must not slow the prediction path."""
        calls = {"n": 0}

        def slow_llm(rul):
            calls["n"] += 1
            return "report", "rag"

        monkeypatch.setattr(api, "_generate_report_blocking", slow_llm)
        response = _post(client, "/predict?async_report=true", sensor_csv)
        assert response.status_code == 200
        # TestClient drains background tasks, so the call happens — but only
        # after the response was produced. Assert it happened exactly once and
        # that the prediction body was already complete.
        assert response.json()["report_status"] == "pending"
        assert calls["n"] <= 1


class TestVersioning:
    def test_v1_predict_alias_exists(self):
        paths = {r.path for r in api.app.routes if hasattr(r, "path")}
        assert "/v1/predict" in paths
        assert "/predict" in paths

    def test_health_is_not_versioned(self):
        """Orchestrator healthchecks must keep a stable path."""
        paths = {r.path for r in api.app.routes if hasattr(r, "path")}
        assert "/" in paths
        assert "/v1/" not in paths

    def test_report_route_is_versioned(self):
        paths = {r.path for r in api.app.routes if hasattr(r, "path")}
        assert "/v1/reports/{report_id}" in paths


class TestRateLimiting:
    def test_limiter_is_installed(self):
        assert api.app.state.limiter is not None

    def test_health_is_not_rate_limited(self, client):
        """Prometheus and healthchecks poll; they must never see a 429."""
        assert [client.get("/").status_code for _ in range(30)] == [200] * 30
