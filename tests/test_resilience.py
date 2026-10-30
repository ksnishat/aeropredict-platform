"""Tests for the rate-limiting and retry helpers.

These are pure-unit tests: no model, no network, no database. They cover the
two behaviours an operator depends on — that a limit actually turns into a 429
with a usable Retry-After, and that only transient errors are retried.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ratelimit  # noqa: E402
import retry as retry_mod  # noqa: E402


class TestRetryPolicy:
    def test_transient_error_is_retried_then_succeeds(self):
        calls = {"n": 0}

        @retry_mod.retry_transient(attempts=3, base_delay=0.001)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

    def test_permanent_error_is_not_retried(self):
        """A validation failure must fail on the first attempt."""
        calls = {"n": 0}

        @retry_mod.retry_transient(attempts=5, base_delay=0.001)
        def bad_input():
            calls["n"] += 1
            raise ValueError("nope")

        with pytest.raises(ValueError):
            bad_input()
        assert calls["n"] == 1

    def test_reraises_after_exhausting_attempts(self):
        calls = {"n": 0}

        @retry_mod.retry_transient(attempts=2, base_delay=0.001)
        def always_down():
            calls["n"] += 1
            raise TimeoutError("down")

        with pytest.raises(TimeoutError):
            always_down()
        assert calls["n"] == 2

    def test_retrying_to_none_returns_default(self):
        @retry_mod.retrying_to_none(attempts=2, default="fallback")
        def optional():
            raise ConnectionError("down")

        assert optional() == "fallback"

    def test_retrying_to_none_returns_real_value(self):
        @retry_mod.retrying_to_none(attempts=2, default="fallback")
        def optional():
            return "real"

        assert optional() == "real"


class TestRateLimit:
    def _app(self, limit: str):
        app = FastAPI()
        ratelimit.install(app)

        @app.get("/probe")
        @ratelimit.limiter.limit(limit)
        def probe(request: Request):
            return {"ok": True}

        return app

    def test_requests_within_limit_are_allowed(self):
        client = TestClient(self._app("5/minute"))
        assert [client.get("/probe").status_code for _ in range(5)] == [200] * 5

    def test_exceeding_limit_returns_429(self):
        client = TestClient(self._app("2/minute"))
        client.get("/probe")
        client.get("/probe")
        response = client.get("/probe")
        assert response.status_code == 429
        assert response.json()["error"] == "rate_limit_exceeded"

    def test_429_carries_retry_after(self):
        client = TestClient(self._app("1/minute"))
        client.get("/probe")
        response = client.get("/probe")
        assert response.status_code == 429
        retry_after = response.headers.get("retry-after")
        assert retry_after is not None
        assert int(retry_after) >= 1

    def test_unlimited_route_is_unaffected(self):
        """Health and metrics must never be throttled — Prometheus scrapes them."""
        app = FastAPI()
        ratelimit.install(app)

        @app.get("/")
        def health():
            return {"status": "ok"}

        client = TestClient(app)
        assert [client.get("/").status_code for _ in range(50)] == [200] * 50


class TestRateLimitKeying:
    def test_api_key_is_hashed_not_stored_raw(self):
        class FakeRequest:
            headers = {"X-API-Key": "super-secret-key"}
            client = None

        key = ratelimit._rate_limit_key(FakeRequest())
        assert "super-secret-key" not in key
        assert len(key) == 32

    def test_different_keys_get_different_budgets(self):
        class FakeRequest:
            def __init__(self, k):
                self.headers = {"X-API-Key": k}
                self.client = None

        a = ratelimit._rate_limit_key(FakeRequest("key-a"))
        b = ratelimit._rate_limit_key(FakeRequest("key-b"))
        assert a != b

    def test_disabled_switch_respected(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
        assert ratelimit._enabled() is False
