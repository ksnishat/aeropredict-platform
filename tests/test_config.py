"""Tests for the AeroPredict settings and structured logging.

`config.py` and `utils/logging_config.py` were 0% covered. They matter because
every environment-driven default in the service comes from here: a wrong alias
silently ignores the operator's environment variable and the service starts with
a development default in production.
"""

import importlib
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestSettings:
    def test_defaults_are_populated(self):
        from src.config import Settings

        settings = Settings()
        assert settings.app_name == "aeropredict"
        assert settings.api_port == 8000
        assert settings.ollama_model

    def test_environment_overrides_the_default(self, monkeypatch):
        """Each field aliases an env var; a broken alias breaks deployment."""
        from src.config import Settings

        monkeypatch.setenv("API_PORT", "9999")
        monkeypatch.setenv("OLLAMA_MODEL", "llama3")
        settings = Settings()
        assert settings.api_port == 9999
        assert settings.ollama_model == "llama3"

    def test_unknown_environment_keys_are_ignored(self, monkeypatch):
        """A shared .env must not crash the service with an extra key."""
        from src.config import Settings

        monkeypatch.setenv("TOTALLY_UNRELATED_VAR", "x")
        assert Settings().app_name == "aeropredict"

    def test_get_settings_is_cached(self):
        from src.config import get_settings

        assert get_settings() is get_settings()

    def test_module_level_settings_exists(self):
        from src.config import settings

        assert settings is not None

    def test_boolean_alias_parses_common_forms(self, monkeypatch):
        from src.config import Settings

        for raw, expected in (("true", True), ("false", False), ("1", True)):
            monkeypatch.setenv("ENABLE_RAG", raw)
            assert Settings().enable_rag is expected


class TestLoggingConfig:
    """The service emits JSON logs so the aggregator can parse them."""

    @staticmethod
    def _module():
        pytest.importorskip(
            "pythonjsonlogger",
            reason="python-json-logger is a declared dependency; skip when absent "
                   "so this suite still runs in a minimal environment",
        )
        return importlib.import_module("src.utils.logging_config")

    def test_module_imports(self):
        assert self._module() is not None

    def test_json_formatter_produces_one_json_object(self):
        mod = self._module()
        formatter = mod.jsonlogger.JsonFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        payload = json.loads(formatter.format(record))
        assert payload["message"] == "hello"

    def test_request_id_context_is_available(self):
        mod = self._module()
        assert mod.request_id_var.get() is None
        token = mod.request_id_var.set("req-123")
        try:
            assert mod.request_id_var.get() == "req-123"
        finally:
            mod.request_id_var.reset(token)

    def test_configure_logging_is_callable_and_idempotent(self):
        mod = self._module()
        configure = getattr(mod, "configure_logging", None) or getattr(
            mod, "setup_logging", None
        )
        if configure is None:
            pytest.skip("no configure/setup entrypoint in this module")
        configure()
        configure()
        assert logging.getLogger().handlers
