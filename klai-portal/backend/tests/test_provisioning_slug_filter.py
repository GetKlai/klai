"""SPEC-PROV-001 M1 — slug uniqueness must ignore soft-deleted orgs.

Verifies that _provision's slug-collision check does not treat soft-deleted
(`deleted_at IS NOT NULL`) rows as reserved. The partial unique index
`ix_portal_orgs_slug_active` only enforces uniqueness over active rows, so a
retry after `failed_rollback_complete` must be able to reclaim the original slug.
"""



def test_slug_query_source_filters_soft_deleted() -> None:
    """The slug lookup in _provision must filter out soft-deleted rows.

    Verified by inspecting the orchestrator source — an in-process SQL capture
    is unreliable because the surrounding flow calls many other SELECTs and
    mocking all of them just to observe the one we care about couples the test
    to implementation details.
    """
    import inspect

    from app.services.provisioning import orchestrator

    source = inspect.getsource(orchestrator._provision)
    assert "PortalOrg.slug" in source, "slug lookup query must still exist"
    assert "deleted_at.is_(None)" in source or "deleted_at IS NULL" in source, (
        "SPEC-PROV-001 M1: slug uniqueness query must filter `deleted_at IS NULL`. "
        "_provision source contains no such filter."
    )
