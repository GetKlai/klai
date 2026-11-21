"""The gap router sits behind the kb.gaps capability and the knowledge_gaps unlock."""

from __future__ import annotations


def test_gaps_router_requires_the_knowledge_gaps_unlock() -> None:
    """The screen is unfinished: every gap endpoint sits behind the per-tenant
    knowledge_gaps unlock on top of the kb.gaps capability."""
    import inspect

    from app.api.app_gaps import router

    features = [
        inspect.getclosurevars(dep.dependency).nonlocals.get("feature")
        for dep in router.dependencies
        if getattr(dep.dependency, "__closure__", None)
    ]
    assert "knowledge_gaps" in features
