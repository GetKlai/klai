"""Every route the application registers is reachable at its own path.

The router dispatches to the first route, in registration order, whose pattern
matches, so a parameterised path such as ``/users/{id}`` registered before a
literal sibling ``/users/me`` with the same method answers every request meant
for the sibling. ``iter_route_contexts`` flattens the included routers into that
dispatch order with their prefixes applied.
"""

from __future__ import annotations

from fastapi.routing import APIRoute, iter_route_contexts

from app.main import app


def test_no_route_is_answered_by_an_earlier_parameterised_route() -> None:
    routes = [
        (ctx.path, ctx.path_regex, ctx.methods or set())
        for ctx in iter_route_contexts(app.routes)
        if isinstance(ctx.original_route, APIRoute)
    ]
    assert any(path == "/api/admin/users/me" for path, _, _ in routes)
    shadowed = [
        f"{sorted(methods & later_methods)} {path} answers {later_path}"
        for index, (path, regex, methods) in enumerate(routes)
        for later_path, _, later_methods in routes[index + 1 :]
        if methods & later_methods and regex.match(later_path)
    ]
    assert shadowed == []
