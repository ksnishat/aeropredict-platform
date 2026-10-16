"""Tests for upload size limiting.

FastAPI spools an UploadFile to disk before the handler runs, so a check inside
the handler is too late to protect memory. The Content-Length dependency rejects
early; read_upload is the authoritative check on the bytes actually received.
"""

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, File, UploadFile
from fastapi.testclient import TestClient


@pytest.fixture
def uploads_module():
    src = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(src))
    mod = importlib.import_module("uploads")
    importlib.reload(mod)
    return mod


def _app(mod):
    app = FastAPI()

    @app.post("/upload", dependencies=[Depends(mod.enforce_content_length)])
    async def upload(file: UploadFile = File(...)):
        data = await mod.read_upload(file)
        return {"bytes": len(data)}

    return app


class TestUploadLimit:
    def test_default_limit_is_ten_mib(self, uploads_module, monkeypatch):
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        assert uploads_module.max_upload_bytes() == 10 * 1024 * 1024

    def test_env_override(self, uploads_module, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "1024")
        assert uploads_module.max_upload_bytes() == 1024

    def test_invalid_env_falls_back_to_default(self, uploads_module, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "not-a-number")
        assert uploads_module.max_upload_bytes() == 10 * 1024 * 1024

    def test_oversized_upload_is_rejected(self, uploads_module, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "1024")
        client = TestClient(_app(uploads_module))
        resp = client.post("/upload", files={"file": ("big.bin", b"x" * 4096)})
        assert resp.status_code == 413

    def test_upload_within_limit_is_accepted(self, uploads_module, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "1024")
        client = TestClient(_app(uploads_module))
        resp = client.post("/upload", files={"file": ("small.bin", b"x" * 512)})
        assert resp.status_code == 200
        assert resp.json()["bytes"] == 512
