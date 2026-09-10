"""Widget-public CORS: the route declares it, the middleware looks it up.

The widget bundle (klai-widget/) runs on the customer's own domain and calls
a handful of Partner API routes cross-origin. Which ones is declared on the
route itself with ``@widget_cors(router, ECHO)`` placed directly above the
``@router.<method>(...)`` decorator, so KlaiCORSMiddleware holds no path
list that a new widget endpoint could be forgotten in (that omission fails
only on customer domains and silently: help.voys.nl, PR #1361).

Two modes, because the CORS ownership genuinely differs:

- ``ECHO``: the middleware answers the preflight and adds
  ``Access-Control-Allow-Origin`` with the origin echoed, credentials never
  allowed. The security boundary on these routes is the widget session JWT.
- ``ROUTE_DECIDES``: the middleware passes the request through untouched;
  the route builds its own CORS headers. ``/widget-config`` needs this: its
  per-widget ``allowed_origins`` gate needs a DB read and must answer a
  disallowed origin WITHOUT an origin echo (SPEC-SEC-CORS-001 AC-10).

The lookup is exact on (method, path); a preflight is looked up with its
``Access-Control-Request-Method``. Nothing here reads FastAPI internals: the
decorator records ``router.routes[-1]``, the route the decorator below it
just registered, through the public ``APIRoute.path`` and ``.methods``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter
from fastapi.routing import APIRoute

ECHO = "echo"
ROUTE_DECIDES = "route"

_REGISTRY: dict[tuple[str, str], str] = {}

F = TypeVar("F", bound=Callable[..., object])


def widget_cors(router: APIRouter, mode: str) -> Callable[[F], F]:
    """Mark the route registered by the ``@router.<method>`` decorator directly below."""

    if mode not in (ECHO, ROUTE_DECIDES):
        raise ValueError(f"widget_cors mode must be ECHO or ROUTE_DECIDES, got {mode!r}")

    def decorate(endpoint: F) -> F:
        route = router.routes[-1] if router.routes else None
        if not isinstance(route, APIRoute) or route.endpoint is not endpoint:
            raise RuntimeError("@widget_cors must sit directly above the @router.<method>(...) decorator")
        for method in route.methods or ():
            key = (method, route.path)
            if _REGISTRY.get(key, mode) != mode:
                raise RuntimeError(f"{method} {route.path} is already declared with mode {_REGISTRY[key]!r}")
            _REGISTRY[key] = mode
        return endpoint

    return decorate


def widget_cors_mode(method: str, path: str) -> str | None:
    """Declared mode for this (method, path), or ``None`` for every other route."""
    return _REGISTRY.get((method.upper(), path))


def registered_widget_routes() -> dict[tuple[str, str], str]:
    """Snapshot of every declared route, for the test that pins the production set."""
    return dict(_REGISTRY)


def _replace_registry(entries: dict[tuple[str, str], str]) -> None:
    """Test hook: swap the registry so a test can register from one router only."""
    _REGISTRY.clear()
    _REGISTRY.update(entries)
