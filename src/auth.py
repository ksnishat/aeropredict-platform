"""API-key authentication for the portfolio services.

Design notes
------------
* **Off by default.** If ``API_KEY`` is unset the dependency is a no-op, so
  local development and the demo stacks behave exactly as before. Auth turns on
  when the operator sets the variable, which is what a deployment would do.
* **Constant-time comparison.** ``secrets.compare_digest`` avoids leaking the
  key length or prefix through response timing.
* **Health stays open.** ``/`` and ``/metrics`` are unauthenticated on purpose:
  a load balancer and Prometheus both need them, and neither can carry a
  credential. They expose no data, only liveness and counters.
* **``/admin/reload-model`` is the reason this exists.** It swaps the served
  model. Unauthenticated, it is a remote model-replacement endpoint.
"""

# `from __future__ import annotations` makes every annotation a string, so the
# PEP 604 unions below are never evaluated at runtime. Without it this module
# would raise "unsupported operand type(s) for |" on Python 3.8, which is what
# the Airflow image runs.
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status

API_KEY_ENV = "API_KEY"


def _configured_key() -> str | None:
    key = os.getenv(API_KEY_ENV, "").strip()
    return key or None


def auth_enabled() -> bool:
    """True when an API key is configured, so callers can report it in /health."""
    return _configured_key() is not None


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """FastAPI dependency enforcing the API key when one is configured.

    Raises 401 when a key is configured and the request does not present it.
    Returns immediately when no key is configured, so the default local
    experience is unchanged.
    """
    expected = _configured_key()
    if expected is None:
        return

    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
