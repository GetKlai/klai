"""The graph backfill must never pick its own tenant.

It ran as `SELECT DISTINCT org_id FROM knowledge.artifacts LIMIT 1` — an
unordered pick from what is now 19 tenants. The script opens a
`cross_org_admin_connection` because it deliberately bypasses RLS, so nothing
downstream would have caught the wrong choice: an operator rebuilding one
customer's graph could spend another customer's share of the shared klai-fast
rate budget writing episodes into their graph.
"""

from __future__ import annotations

import inspect

from knowledge_ingest import backfill


def test_org_id_is_a_required_argument():
    signature = inspect.signature(backfill.main)
    assert "org_id" in signature.parameters, "the tenant is discovered, not given"
    assert signature.parameters["org_id"].default is inspect.Parameter.empty, (
        "org_id has a default -- a missing --org-id would silently pick a tenant"
    )


def test_the_tenant_is_never_discovered():
    source = inspect.getsource(backfill)
    assert "SELECT DISTINCT org_id" not in source, (
        "the tenant is still being discovered from the table"
    )


def test_the_cli_refuses_to_run_without_a_tenant():
    """argparse must reject a bare invocation rather than defaulting."""
    source = inspect.getsource(backfill)
    assert '"--org-id"' in source
    assert "required=True" in source


def test_a_missing_tenant_stops_before_any_work():
    """An org with no artifacts must abort, not fall through to a scan."""
    source = inspect.getsource(backfill.main)
    assert "EXISTS" in source, "existence is not checked before processing"
    assert "nothing to backfill" in source
