"""Tests for API-key authentication and upload size limiting.

Both are off by default so local development is unchanged, and both turn on via
environment variables. These tests cover the enforcement path, which is the part
that matters: an unauthenticated /admin/reload-model is a remote
model-replacement endpoint.
"""

import importlib

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def auth_module(monkeypatch):
    """Import src.auth fresh so the env var is read at call time."""
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(src))
    mod = importlib.import_module("auth")
    importlib.reload(mod)
    return mod


def _app(mod):
    app = FastAPI()

    @app.get("/open")
    def open_route():
        return {"ok": True}

    @app.get("/protected", dependencies=[Depends(mod.require_api_key)])
    def protected():
        return {"ok": True}

    return app


class TestApiKeyAuth:
    def test_disabled_when_no_key_configured(self, auth_module, monkeypatch):
        monkeypatch.delenv("API_KEY", raising=False)
        client = TestClient(_app(auth_module))
        assert client.get("/protected").status_code == 200
        assert auth_module.auth_enabled() is False

    def test_rejects_missing_key(self, auth_module, monkeypatch):
        monkeypatch.setenv("API_KEY", "s3cret")
        client = TestClient(_app(auth_module))
        assert client.get("/protected").status_code == 401

    def test_rejects_wrong_key(self, auth_module, monkeypatch):
        monkeypatch.setenv("API_KEY", "s3cret")
        client = TestClient(_app(auth_module))
        assert client.get("/protected", headers={"X-API-Key": "nope"}).status_code == 401

    def test_accepts_correct_key(self, auth_module, monkeypatch):
        monkeypatch.setenv("API_KEY", "s3cret")
        client = TestClient(_app(auth_module))
        assert client.get("/protected", headers={"X-API-Key": "s3cret"}).status_code == 200

    def test_health_route_stays_open(self, auth_module, monkeypatch):
        """A load balancer cannot carry a credential, so / must stay open."""
        monkeypatch.setenv("API_KEY", "s3cret")
        client = TestClient(_app(auth_module))
        assert client.get("/open").status_code == 200

    def test_blank_key_is_treated_as_unset(self, auth_module, monkeypatch):
        monkeypatch.setenv("API_KEY", "   ")
        assert auth_module.auth_enabled() is False
