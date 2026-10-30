"""Tests for the /v1 route aliasing.

The contract these tests protect: adding a version must never remove or alter
an existing path, and every aliased path must resolve to the same endpoint
function so there is only one implementation to fix.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import versioning  # noqa: E402


@pytest.fixture
def app():
    app = FastAPI()

    @app.get("/")
    def health():
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics():
        return "m"

    @app.post("/predict")
    def predict():
        return {"predicted": True}

    @app.get("/drift")
    def drift():
        return {"drift": True}

    return app


class TestVersioning:
    def test_routes_are_aliased_under_v1(self, app):
        n = versioning.install(app)
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/v1/predict" in paths
        assert "/v1/drift" in paths
        assert n == 2

    def test_original_paths_still_work(self, app):
        """This is the whole point: aliasing must be additive."""
        versioning.install(app)
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/predict" in paths
        assert "/drift" in paths

    def test_infrastructure_paths_are_not_versioned(self, app):
        """Prometheus and healthchecks are configured once; keep them stable."""
        versioning.install(app)
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/v1/" not in paths
        assert "/v1/metrics" not in paths
        assert "/metrics" in paths

    def test_admin_paths_are_not_versioned(self, app):
        @app.post("/admin/reload-model")
        def reload_model():
            return {"reloaded": True}

        versioning.install(app)
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/v1/admin/reload-model" not in paths

    def test_alias_shares_the_endpoint_function(self, app):
        """One implementation, so a fix applies to both paths."""
        versioning.install(app)
        by_path = {r.path: r.endpoint for r in app.routes if hasattr(r, "path")}
        assert by_path["/v1/predict"] is by_path["/predict"]

    def test_no_double_versioning(self, app):
        versioning.install(app)
        versioning.install(app)
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert paths.count("/v1/predict") == 1

    def test_v1_alias_actually_serves(self, app):
        versioning.install(app)
        client = TestClient(app)
        assert client.post("/v1/predict").json() == {"predicted": True}
        assert client.post("/predict").json() == {"predicted": True}

    def test_v1_appears_in_openapi_schema(self, app):
        versioning.install(app)
        schema = app.openapi()
        assert "/v1/predict" in schema["paths"]
        assert "/predict" in schema["paths"]
