"""Request rate limiting.

A single-prediction endpoint that costs ~10 ms of CPU is trivial to exhaust:
one client in a loop starves every other caller, and the drift/LLM endpoints
cost far more. The limits here are deliberately per-route rather than global,
because the cheap health and metrics endpoints are scraped by Prometheus every
few seconds and must never be throttled.

The default limits are configurable via environment variables so an operator can
raise them under a real load test without a code change:

    RATE_LIMIT_PREDICT   default "60/minute"
    RATE_LIMIT_DRIFT     default "30/minute"
    RATE_LIMIT_ENABLED   default "1"; set to "0" to disable entirely

Limits are keyed on the API key when one is present, otherwise on the client IP.
Keying on the caller rather than the address means a shared NAT does not pool
everyone into one budget.
"""

import math
import os
import re

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _rate_limit_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    Prefer the API key: it is the real unit of accountability, and it keeps a
    shared egress IP from collapsing several clients into one bucket. Fall back
    to the client address for unauthenticated routes.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        # Hash so the raw credential never appears in limiter state or logs.
        import hashlib

        return hashlib.sha256(api_key.encode()).hexdigest()[:32]
    return get_remote_address(request)


def _enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


limiter = Limiter(
    key_func=_rate_limit_key,
    # Memory is correct for a single-process service. A multi-replica deployment
    # needs a shared backend (storage_uri="redis://...") or each replica grants
    # the full quota independently.
    storage_uri=os.getenv("RATE_LIMIT_STORAGE", "memory://"),
    enabled=_enabled(),
    # headers_enabled=False on purpose. With it True, slowapi injects
    # X-RateLimit-* into the return value of every decorated route, which
    # raises `parameter 'response' must be an instance of Response` unless each
    # handler also declares a `response: Response` argument. That would mean
    # changing the signature of every rate-limited endpoint. The 429 path still
    # carries Retry-After, which is the header a client actually needs.
    headers_enabled=False,
    default_limits=[],
)

_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

PREDICT_LIMIT = os.getenv("RATE_LIMIT_PREDICT", "60/minute")
DRIFT_LIMIT = os.getenv("RATE_LIMIT_DRIFT", "30/minute")
REPORT_LIMIT = os.getenv("RATE_LIMIT_REPORT", "30/minute")
READ_LIMIT = os.getenv("RATE_LIMIT_READ", "120/minute")


def install(app) -> None:
    """Attach the limiter and its 429 handler to a FastAPI app.

    Both steps are required: registering the limiter supplies the per-route
    decorators with state, and the handler turns a breach into a JSON 429 with
    ``Retry-After`` instead of an unhandled exception.
    """
    from slowapi.errors import RateLimitExceeded
    from starlette.responses import JSONResponse

    def _on_rate_limit_exceeded(request, exc: RateLimitExceeded):
        """Return a 429 that tells the client how long to wait.

        slowapi's stock handler is not used because it calls
        ``_inject_headers``, which asserts a ``response`` object exists on the
        request state — something that is only set up when ``headers_enabled``
        is True. Building the response directly keeps the useful part
        (``Retry-After``) without that coupling.
        """
        detail = str(getattr(exc, "detail", "rate limit exceeded"))
        # slowapi renders the breached limit into `detail` as e.g.
        # "3 per 1 minute". Parsing it here is what makes Retry-After work
        # without `headers_enabled`; request.state.view_rate_limit is only
        # populated when header injection is on.
        retry_after = None
        window_seconds = 60
        match = re.search(r"per\s+(\d+)\s*(second|minute|hour|day)", detail)
        if match:
            window_seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
        amount = re.match(r"\s*(\d+)", detail)
        if amount and int(amount.group(1)) > 0:
            retry_after = max(1, math.ceil(window_seconds / int(amount.group(1))))
        headers = {"Retry-After": str(retry_after)} if retry_after else {}
        return JSONResponse(
            {"error": "rate_limit_exceeded", "detail": detail},
            status_code=429,
            headers=headers,
        )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _on_rate_limit_exceeded)
