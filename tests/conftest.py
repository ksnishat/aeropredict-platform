"""Shared pytest fixtures for all AeroPredict tests."""

import importlib.util
import os
import tempfile

import pytest

# Optional dependencies the suite exercises. They are declared in
# requirements.txt and therefore pinned in requirements.lock, so CI has them.
# A developer env created before the lock may not, and without this guard the
# failure reads as 27 broken tests instead of "three packages are missing".
_OPTIONAL = {
    "bentoml": "tests/test_rul_service.py",
    "pythonjsonlogger": "tests/test_pipeline_scripts.py",
    "pytorch_lightning": "tests/test_train_lightning.py",
}


def pytest_ignore_collect(collection_path, config):
    """Skip a test module when its optional dependency is not installed."""
    path = str(collection_path)
    for module, test_file in _OPTIONAL.items():
        if path.endswith(test_file) and importlib.util.find_spec(module) is None:
            return True
    return None


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
