"""REQ-13 (Finding B-6, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): admin widget
activity endpoints must enforce the platform-unlock gate.

AC13.1, AC13.2, AC13.3 — list_widget_conversations, get_widget_conversation,
widget_activity_stats must include Depends(require_platform_unlocked("widgets"))
so admins of revoked tenants cannot read conversation logs / stats for widgets
that already exist.

Route-introspection tests: walk each route's resolved dependant tree and
assert that one of the dependencies closes over the literal string ``"widgets"``
(only ``require_platform_unlocked`` produces such a closure in this codebase).
"""

from __future__ import annotations

import pytest
from fastapi.dependencies.models import Dependant

from app.main import app

# Routes that REQ-13 protects.
REQ13_ROUTE_PATHS: tuple[str, ...] = (
    "/api/admin/widgets/{widget_id}/conversations",
    "/api/admin/widgets/{widget_id}/conversations/{conv_id}",
    "/api/admin/widgets/{widget_id}/stats",
)


def _dep_closes_over_widgets(dep: Dependant) -> bool:
    """Return True iff ``dep`` (or any sub-dep) holds the literal "widgets" in its closure."""
    call = dep.call
    if call is not None:
        closure = getattr(call, "__closure__", None) or ()
        for cell in closure:
            try:
                if cell.cell_contents == "widgets":
                    return True
            except ValueError:
                continue
    for child in dep.dependencies:
        if _dep_closes_over_widgets(child):
            return True
    return False


def _find_route_by_path(path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            for child in getattr(original_router, "routes", []):
                if getattr(child, "path", None) == path:
                    return child
    widget_paths = sorted(
        {
            child_path
            for r in app.routes
            for child_path in (
                [getattr(r, "path", "")]
                + [
                    getattr(child, "path", "")
                    for child in getattr(getattr(r, "original_router", None), "routes", [])
                ]
            )
            if "widget" in child_path
        }
    )
    raise AssertionError(f"Route {path!r} not registered on app.routes. widget-related paths found: {widget_paths}")


@pytest.mark.parametrize("path", REQ13_ROUTE_PATHS)
def test_admin_widget_activity_route_has_platform_unlock_gate(path: str) -> None:
    """AC13.1/AC13.2/AC13.3 — every admin widget activity route MUST include
    Depends(require_platform_unlocked("widgets")) in its dependency tree.
    """
    route = _find_route_by_path(path)
    assert _dep_closes_over_widgets(route.dependant), (
        f"Route {path!r} is missing the platform-unlock gate "
        f"(Depends(require_platform_unlocked('widgets'))). REQ-13 requires this gate "
        f"so admins of revoked tenants cannot read conversation/stats data."
    )
