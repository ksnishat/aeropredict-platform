"""Shared pytest fixtures for all AeroPredict tests."""

import os
import tempfile
import pytest


@pytest.fixture(scope="session")
def tmp_dir():
    """Create a temporary directory for the test session."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture(scope="session")
def data_dir(tmp_dir):
    """Create data subdirectory."""
    d = os.path.join(tmp_dir, "data")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def models_dir(tmp_dir):
    """Create models subdirectory."""
    d = os.path.join(tmp_dir, "models")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def logs_dir(tmp_dir):
    """Create logs subdirectory."""
    d = os.path.join(tmp_dir, "logs")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture
def env_vars(monkeypatch):
    """Set environment variables for tests."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")