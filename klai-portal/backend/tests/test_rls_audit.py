"""Static audit — every module that queries an RLS-strict model must
establish tenant context (set_tenant, tenant_scoped_session, cross_org_session,
or a FastAPI dependency that does one of those).

Rationale
---------
The 2026-04-21 production outage: /api/me/sar-export queried portal_groups
and portal_knowledge_bases without set_tenant. PostgreSQL's strict RLS policy
(post_deploy_rls_raise_on_missing_context.sql) raised
InsufficientPrivilegeError, the /callback page rendered "Login failed HTTP
500" for every user.

SQLite backs the pytest suite, so individual endpoint tests cannot exercise
the real policy. This static test closes the gap: if a new module imports
an RLS-strict model and issues a query without any tenant-context helper
in scope, CI fails.

The test is intentionally conservative (false positives > false negatives)
and uses a small allow-list for modules that legitimately query RLS tables
through an alternative mechanism the grep cannot see.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Models whose underlying tables carry the strict-raise RLS policy.
# If any of these is imported and used in a module, some form of tenant
# context MUST also be set up somewhere in that module.
_STRICT_MODELS: frozenset[str] = frozenset(
    {
        "PortalGroup",
        "PortalGroupProduct",
        "PortalGroupMembership",
        "PortalGroupKBAccess",
        "PortalKnowledgeBase",
        "PortalUserProduct",
        "PortalUserKBAccess",
        "PortalTaxonomyNode",
        "PortalTaxonomyProposal",
        "PortalRetrievalGap",
        "PortalKbTombstone",
        "Widget",
        "WidgetKbAccess",
        "PartnerApiKey",
        "PartnerApiKeyKbAccess",
        "VexaMeeting",
    }
)

# Any of these markers in a module's source means tenant context is
# established somewhere in its flow. Conservative — a module that calls
# one of these functions but on the WRONG session is not caught, but
# that is a code-review problem, not a static-grep one.
_TENANT_MARKERS: tuple[str, ...] = (
    "set_tenant(",
    "tenant_scoped_session(",
    "cross_org_session(",
    "_get_caller_org(",
    "require_partner_context(",
    "require_admin_api_key(",
    "get_effective_products(",  # self-heals tenant internally
)

# Modules that legitimately query RLS models without a direct tenant marker
# in their own source. The justification must stay in this map.
_ALLOWLIST: dict[str, str] = {
    # access.py is a service layer; every caller is an authenticated API
    # route that goes through _get_caller_org before calling these helpers.
    "app/services/access.py": (
        "service layer — all callers pass a db session that already has tenant context set via _get_caller_org"
    ),
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _module_queries_strict_rls(src: str) -> bool:
    if "db.execute" not in src and "session.execute" not in src and "db.add(" not in src:
        return False
    return any(re.search(rf"\b{m}\b", src) for m in _STRICT_MODELS)


def _has_tenant_marker(src: str) -> bool:
    return any(marker in src for marker in _TENANT_MARKERS)


def test_every_module_with_strict_rls_queries_has_tenant_context() -> None:
    backend = _project_root() / "app"
    offenders: list[str] = []
    for path in sorted(backend.rglob("*.py")):
        if "models" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_project_root()).as_posix()
        if rel in _ALLOWLIST:
            continue
        src = path.read_text(encoding="utf-8")
        if not _module_queries_strict_rls(src):
            continue
        if _has_tenant_marker(src):
            continue
        offenders.append(rel)

    assert not offenders, (
        "Modules querying RLS-strict models without any tenant-context helper:\n"
        + "\n".join(f"  - {p}" for p in offenders)
        + "\n\nFix by calling set_tenant(db, org_id) / tenant_scoped_session() / "
        "cross_org_session() before the query, or add a justified entry to "
        "_ALLOWLIST in this test."
    )


@pytest.mark.parametrize("path,reason", _ALLOWLIST.items())
def test_allowlist_entries_still_exist(path: str, reason: str) -> None:
    """Guard against stale allowlist entries: the file must still exist and
    still contain at least one RLS-strict model reference. If not, drop it
    from the allowlist to keep the audit honest.
    """
    full = _project_root() / path
    assert full.exists(), f"Allowlisted path no longer exists: {path}"
    src = full.read_text(encoding="utf-8")
    assert any(re.search(rf"\b{m}\b", src) for m in _STRICT_MODELS), (
        f"{path} no longer queries any RLS-strict model — remove from _ALLOWLIST. (reason was: {reason})"
    )
