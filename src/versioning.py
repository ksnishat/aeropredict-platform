"""API version aliasing.

Every endpoint is currently served at an unversioned path (``/predict``). That
is fine until the response shape has to change: a client cannot express which
contract it wants, so any change is a breaking one.

Rather than duplicate every handler under ``/v1``, this module re-exposes the
existing routes under a version prefix. Two properties matter:

* **The old paths keep working.** Adding a version must not break the clients,
  judges, and Docker healthchecks that already call ``/predict``. Aliasing is
  additive by construction.
* **There is one implementation.** The clones share the original endpoint
  function, so a bug fixed once is fixed everywhere. The only thing that
  differs is the path.

The alias is a copy, not a redirect: a 307 would preserve no contract either
(it just moves the client), and several HTTP clients do not follow 307 for
POST bodies.

This is deliberately a *forward* alias (``/predict`` -> also ``/v1/predict``)
rather than the reverse. ``/v1/predict`` is the future-proof name; the
unversioned path is the legacy one and can be retired once callers migrate.
"""

import logging

logger = logging.getLogger(__name__)

DEFAULT_PREFIX = "/v1"

# Paths that are infrastructure rather than API surface. Prometheus scrapes
# /metrics and healthchecks hit /; neither should be versioned, because the
# scraper and the orchestrator are configured once and expect a stable path.
_UNVERSIONED_EXACT = {"/", "/metrics", "/docs", "/redoc", "/openapi.json"}
_UNVERSIONED_PREFIXES = ("/docs/", "/redoc/", "/admin/")


def _should_alias(path: str) -> bool:
    if path in _UNVERSIONED_EXACT:
        return False
    if path.startswith(_UNVERSIONED_PREFIXES):
        return False
    # Already versioned: do not produce /v1/v1/predict.
    return not path.startswith(f"{DEFAULT_PREFIX}/")


def install(app, prefix: str = DEFAULT_PREFIX) -> int:
    """Re-register the app's routes under ``prefix``.

    Returns the number of routes aliased, which the tests assert on so a future
    refactor cannot silently stop aliasing a new endpoint.
    """
    from fastapi.routing import APIRoute

    # Idempotent: installing twice must not duplicate routes. This matters
    # because a module can be re-imported (uvicorn --reload, a test that
    # rebuilds the app) and duplicate POST routes change dispatch order.
    installed = getattr(app.state, "_version_prefixes", set())
    if prefix in installed:
        return 0

    existing = {
        route.path for route in app.router.routes if isinstance(route, APIRoute)
    }
    originals = [
        route
        for route in list(app.router.routes)
        if isinstance(route, APIRoute)
        and _should_alias(route.path)
        and f"{prefix}{route.path}" not in existing
    ]

    aliased = 0
    for route in originals:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            app.add_api_route(
                path=f"{prefix}{route.path}",
                endpoint=route.endpoint,
                methods=[method],
                response_model=route.response_model,
                status_code=route.status_code,
                tags=route.tags,
                dependencies=route.dependencies,
                summary=route.summary,
                description=route.description,
                response_description=route.response_description,
                deprecated=route.deprecated,
                operation_id=f"v1_{route.operation_id or route.name}",
                include_in_schema=True,
            )
            aliased += 1

    app.state._version_prefixes = installed | {prefix}
    logger.info("Aliased %d routes under %s", aliased, prefix)
    return aliased
