"""Retry helpers for outbound calls.

Every service this project talks to (Ollama, MLflow, MinIO, Postgres) can fail
transiently: a container restart, a model still loading, a cold connection
pool. Without retries those failures surface as a 500 on a request that would
have succeeded a second later, and an operator has no signal that it was
transient rather than broken.

The policy here is deliberately *not* a blanket retry-everything:

* Only exceptions that are plausibly transient are retried. A 404 or a
  validation error is retried zero times, because waiting will not fix it —
  retrying those just multiplies latency and hides the real cause.
* Backoff is exponential with jitter. Jitter matters when several workers fail
  at once (a dependency restart): without it they retry in lockstep and hammer
  the recovering service.
* A capped number of attempts keeps the worst-case latency bounded and
  predictable, which matters because these calls sit inside HTTP handlers.

Usage:

    from retry import retry_transient

    @retry_transient(attempts=3)
    def call_minio():
        ...
"""

import functools
import logging
import os

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

# Attempts include the first try. 3 means "one initial call, two retries".
DEFAULT_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))
DEFAULT_BASE_DELAY = float(os.getenv("RETRY_BASE_DELAY", "0.5"))
DEFAULT_MAX_DELAY = float(os.getenv("RETRY_MAX_DELAY", "8.0"))


def _transient_errors() -> tuple[type[BaseException], ...]:
    """Exception types worth retrying.

    Built lazily and defensively: importing a client library that is not
    installed should downgrade the retry to the generic types rather than fail
    at import time and break the whole module.
    """
    types: list[type[BaseException]] = [
        ConnectionError,
        TimeoutError,
        OSError,
    ]
    try:
        import httpx

        types += [httpx.TransportError, httpx.TimeoutException]
    except ImportError:
        pass
    try:
        import requests.exceptions as rexc

        types += [
            rexc.ConnectionError,
            rexc.Timeout,
            rexc.ChunkedEncodingError,
        ]
    except ImportError:
        pass
    try:
        from urllib3.exceptions import HTTPError as Urllib3HTTPError

        types.append(Urllib3HTTPError)
    except ImportError:
        pass
    try:
        from sqlalchemy.exc import (
            DBAPIError,
            DisconnectionError,
            OperationalError,
        )

        types += [OperationalError, DisconnectionError, DBAPIError]
    except ImportError:
        pass
    return tuple(types)


_RETRY_ON = _transient_errors()


def _log_retry(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "transient failure in %s (attempt %d/%s): %s",
        getattr(retry_state.fn, "__qualname__", retry_state.fn),
        retry_state.attempt_number,
        getattr(retry_state, "max_attempt_number", "?"),
        exc.__class__.__name__ if exc else "unknown",
    )


def retry_transient(attempts: int = DEFAULT_ATTEMPTS,
                    base_delay: float = DEFAULT_BASE_DELAY,
                    max_delay: float = DEFAULT_MAX_DELAY):
    """Retry a callable on transient errors only.

    Non-transient exceptions propagate on the first attempt, unchanged — callers
    rely on that to distinguish "the model rejected this input" from "the model
    was not reachable".
    """
    return retry(
        reraise=True,
        stop=stop_after_attempt(max(1, attempts)),
        # `multiplier`/`jitter` rather than the deprecated `initial`. Jitter is
        # set equal to the base delay so simultaneous failures (all workers hit
        # the same dependency restart) fan out instead of retrying in lockstep.
        wait=wait_exponential_jitter(
            multiplier=max(0.0, base_delay),
            max=max_delay,
            jitter=max(0.0, base_delay),
        ),
        retry=retry_if_exception_type(_RETRY_ON),
        before_sleep=_log_retry,
    )


def retrying_to_none(attempts: int = DEFAULT_ATTEMPTS, default=None):
    """Decorator for optional operations that must never break the caller.

    A report or a metric write failing should degrade the feature, not the
    request. This swallows the final exception and returns ``default`` while
    still logging it, so the failure is visible in logs without a 500.
    """

    def decorator(func):
        @retry_transient(attempts=attempts)
        @functools.wraps(func)
        def inner(*args, **kwargs):
            return func(*args, **kwargs)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return inner(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - degrade, never crash
                logger.warning(
                    "%s failed after %d attempts (%s); returning default",
                    func.__qualname__,
                    attempts,
                    exc,
                )
                return default

        return wrapper

    return decorator
