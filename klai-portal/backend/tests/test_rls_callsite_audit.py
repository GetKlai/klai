"""Static audit: every SELECT/INSERT/UPDATE/DELETE on a category-D model
must sit in a function that either receives a pinned+tenant-scoped
session, or explicitly calls set_tenant / tenant_scoped_session /
cross_org_session.

This is a belt-and-braces complement to the runtime RLS guard — it fails
CI when a new query path is added that would silently 42501 in production
(or silently 0-filter if strict-mode ever rolls back).

The audit is intentionally approximate. False positives are expected when
a helper defers tenant setup to its caller; allow-list those via the
ALLOWED_CALLERS set, always with a justification comment.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

CATEGORY_D_MODELS: frozenset[str] = frozenset(
    {
        "PortalKnowledgeBase",
        "PortalGroup",
        "PortalGroupProduct",
        "PortalGroupKBAccess",
        "PortalKbTombstone",
        "PortalUserKBAccess",
        "PortalUserProduct",
        "PortalRetrievalGap",
        "PortalTaxonomyNode",
        "PortalTaxonomyProposal",
        "VexaMeeting",
    }
)

# Functions that are KNOWN to take a pre-tenant-scoped session. The audit
# accepts any query-bearing function that calls one of these — the
# contract is: "this function sets up tenant context for its body".
TENANT_ESTABLISHING_CALLS: frozenset[str] = frozenset(
    {
        "set_tenant",
        "tenant_scoped_session",
        "cross_org_session",
        "_get_caller_org",  # FastAPI dependency that calls set_tenant internally
        "get_partner_key",  # same, for partner auth
        "get_partner_auth_context",  # widget session token path
    }
)

# Helper functions that accept a session from a caller that has already
# scoped tenant context. Query calls inside these helpers are OK because
# the helper is documented to require tenant setup upstream. One entry per
# helper, with a one-line justification.
ALLOWED_HELPER_FUNCTIONS: frozenset[str] = frozenset(
    {
        # app/services/access.py — all entry points take org_id parameter
        # and are only called from routes that ran _get_caller_org first.
        "_accessible_meetings_filter",
        "get_meetings_for_user",
        "count_meetings_for_user",
        "can_write_meeting",
        "user_can_access_kb",
        "get_user_role_for_kb",
        "get_kb_users_for_user",
        "get_user_kbs",
        # app/services/entitlements.py — self-heals tenant context.
        "get_effective_products",
        # app/services/gap_rescorer.py — calls set_tenant internally
        # now (post-2026-04-21 fix).
        "rescore_open_gaps",
        # app/services/default_knowledge_bases.py — requires pinned
        # session + calls set_tenant itself.
        "ensure_default_knowledge_bases",
        "create_default_org_kb",
        "create_default_personal_kb",
        # app/core/system_groups.py — calls set_tenant itself.
        "create_system_groups",
        # app/api/app_knowledge_bases.py — all helpers take org_id and
        # are only reachable via _get_caller_org routes.
        "_get_kb_or_404",
        "_resolve_personal_kb",
        "_resolve_org_kb",
        "_require_owner",
        "_get_non_system_group_or_404",
        # app/api/app_knowledge_sources.py — SPEC-KB-SOURCES-001. Takes org
        # parameter; only called after _get_caller_org runs set_tenant at
        # the route level (add_url_source / add_youtube_source / add_text_source).
        "_get_writable_kb_or_raise",
        # app/api/knowledge_bases.py — org-scoped helper.
        "_get_kb_or_404_by_id",
        # app/services/recording_cleanup.py — cleanup_recording uses
        # tenant_scoped_session explicitly.
        "cleanup_recording",
        # app/services/knowledge_adapter.py — takes org_id parameter;
        # callers in internal.py run set_tenant upstream.
        "find_vexa_meeting_by_native_id",
        # app/api/admin_api_keys.py + admin_widgets.py — _validate_kb_ids
        # takes org_id, is only called from admin routes under _require_admin
        # which runs _get_caller_org.
        "_validate_kb_ids",
        # app/api/connectors.py — takes org_id, called from connector routes
        # under _get_caller_org.
        "_get_kb_for_org",
        # app/api/dependencies.py — group-admin check helpers take org_id,
        # invoked from routes that already ran _get_caller_org.
        "_require_admin_or_group_admin",
        "_require_admin_or_group_manager",
        # app/api/groups.py — org-scoped helper, called from routes under
        # _get_caller_org.
        "_get_group_or_404",
        # app/api/meetings.py — response builder called from _get_caller_org
        # routes that already set_tenant.
        "_build_meeting_response",
        # app/api/partner.py — helpers called from routes with
        # Depends(get_partner_key).
        "_resolve_kb_slugs",
        # app/api/taxonomy.py — helpers called from routes that ran
        # _get_caller_org + set_tenant (taxonomy routes explicitly call
        # set_tenant at entry).
        "_check_circular_reference",
        "_execute_proposal_action",
        "_execute_merge",
        "_execute_split",
        "_execute_rename",
        # app/services/access.py — get_accessible_kb_slugs takes org_id,
        # only called from app_knowledge_bases routes under _get_caller_org.
        "get_accessible_kb_slugs",
        # app/services/access.py — accept org_id parameter; called from
        # meetings.py routes under _get_caller_org which already ran
        # set_tenant for this session.
        "get_accessible_meetings",
        "count_accessible_meetings",
    }
)

# Files excluded from the audit entirely. Tests are excluded (they use
# mocked sessions); model files don't make queries; scripts are
# one-off migrations.
EXCLUDED_PATH_PARTS: tuple[str, ...] = (
    "/tests/",
    "/models/",
    "/alembic/",
    "/scripts/",
    "/.venv/",
    "__pycache__",
)

BACKEND_APP: Path = Path(__file__).parent.parent / "app"


def _iter_python_files() -> Iterable[Path]:
    for path in BACKEND_APP.rglob("*.py"):
        as_str = str(path).replace("\\", "/")
        if any(part in as_str for part in EXCLUDED_PATH_PARTS):
            continue
        yield path


class _QueryVisitor(ast.NodeVisitor):
    """Walk a module, record functions that query category-D models
    without establishing tenant context.
    """

    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path
        self.offending: list[tuple[str, int, str]] = []
        self._fn_stack: list[tuple[str, bool, bool]] = []  # (name, queries_cat_d, establishes_tenant)

    # ---- helpers -----------------------------------------------------

    @staticmethod
    def _call_target_name(node: ast.Call) -> str | None:
        target = node.func
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            return target.attr
        return None

    @staticmethod
    def _references_cat_d(node: ast.AST) -> str | None:
        """Return the model name if this AST node references a cat-D model,
        else None.
        """
        # select(PortalKnowledgeBase), select(PortalKnowledgeBase.id), etc.
        if isinstance(node, ast.Call):
            for arg in node.args:
                hit = _QueryVisitor._references_cat_d(arg)
                if hit:
                    return hit
            for kw in node.keywords:
                hit = _QueryVisitor._references_cat_d(kw.value)
                if hit:
                    return hit
        if isinstance(node, ast.Attribute):
            return _QueryVisitor._references_cat_d(node.value)
        if isinstance(node, ast.Name) and node.id in CATEGORY_D_MODELS:
            return node.id
        return None

    # ---- traversal ---------------------------------------------------

    def _enter_fn(self, name: str) -> None:
        self._fn_stack.append((name, False, False))

    def _leave_fn(self) -> None:
        name, queries, establishes = self._fn_stack.pop()
        if queries and not establishes and name not in ALLOWED_HELPER_FUNCTIONS:
            self.offending.append((str(self.source_path), 0, name))

    def _has_tenant_establishing_dependency(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """True if the function signature includes `= Depends(<tenant_setter>)`
        — FastAPI resolves the dependency before the body runs, so the body
        can assume tenant context is set.
        """
        for default in node.args.defaults + node.args.kw_defaults:
            if default is None:
                continue
            if not isinstance(default, ast.Call):
                continue
            if self._call_target_name(default) not in {"Depends", "Security"}:
                continue
            for arg in default.args:
                if isinstance(arg, ast.Name) and arg.id in TENANT_ESTABLISHING_CALLS:
                    return True
        return False

    def _function_enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._enter_fn(node.name)
        if self._has_tenant_establishing_dependency(node):
            name, queries, _ = self._fn_stack[-1]
            self._fn_stack[-1] = (name, queries, True)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_enter(node)
        self.generic_visit(node)
        if self.offending and self.offending[-1][2] == node.name and self.offending[-1][1] == 0:
            self.offending[-1] = (self.offending[-1][0], node.lineno, node.name)
        self._leave_fn()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_enter(node)
        self.generic_visit(node)
        if self.offending and self.offending[-1][2] == node.name and self.offending[-1][1] == 0:
            self.offending[-1] = (self.offending[-1][0], node.lineno, node.name)
        self._leave_fn()

    def visit_Call(self, node: ast.Call) -> None:
        if not self._fn_stack:
            self.generic_visit(node)
            return

        callee = self._call_target_name(node)
        # Only TENANT_ESTABLISHING_CALLS actually set app.current_org_id on
        # the current session. Calling an ALLOWED_HELPER_FUNCTIONS entry does
        # NOT establish tenant — those helpers assume tenant is already set
        # by the caller, OR they open their own independent session. Treating
        # them as tenant-establishing would mask silent RLS failures in the
        # caller's own session (see recording_cleanup_loop incident on
        # 2026-04-22: it called cleanup_recording which opens its own
        # tenant_scoped_session, but the loop's OWN SELECT on VexaMeeting
        # still ran without tenant context and got 42501'd in production).
        if callee in TENANT_ESTABLISHING_CALLS:
            name, queries, _ = self._fn_stack[-1]
            self._fn_stack[-1] = (name, queries, True)

        # Detect query builder calls: select(...), update(...), delete(...),
        # insert(...). SQLAlchemy's ORM + Core variants all go through these.
        if callee in {"select", "update", "delete", "insert"}:
            hit = self._references_cat_d(node)
            if hit:
                name, _, establishes = self._fn_stack[-1]
                self._fn_stack[-1] = (name, True, establishes)

        # Direct ORM construction: PortalGroup(...), PortalKnowledgeBase(...)
        if callee in CATEGORY_D_MODELS:
            name, _, establishes = self._fn_stack[-1]
            self._fn_stack[-1] = (name, True, establishes)

        self.generic_visit(node)


def _audit_file(path: Path) -> list[tuple[str, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    visitor = _QueryVisitor(path)
    visitor.visit(tree)
    return visitor.offending


def test_category_d_queries_always_establish_tenant_context():
    """Every function that queries a category-D model must either:
      - be a FastAPI route whose dependency chain includes _get_caller_org /
        get_partner_key (tenant set by FastAPI before body runs),
      - explicitly call set_tenant / tenant_scoped_session / cross_org_session,
      - or be in ALLOWED_HELPER_FUNCTIONS (documented to require upstream
        tenant context).

    When this test fails with a new file/function, the right fix is almost
    always to call set_tenant at the top of that function — not to add it to
    ALLOWED_HELPER_FUNCTIONS. Only add to the allow-list if the helper is
    guaranteed to run inside an already-scoped session and you document
    why in a comment next to the entry.
    """
    offenders: list[tuple[str, int, str]] = []
    for path in _iter_python_files():
        offenders.extend(_audit_file(path))

    if offenders:
        formatted = "\n".join(f"  {p}:{lineno} in {name}()" for p, lineno, name in offenders)
        pytest.fail(
            "Functions query a category-D RLS model without establishing tenant "
            "context. Fix by calling set_tenant/tenant_scoped_session/"
            "cross_org_session at the top, or add to ALLOWED_HELPER_FUNCTIONS "
            f"with a justification:\n{formatted}"
        )


def test_category_d_models_match_rls_guard_constant():
    """RLS_DML_TABLES in app.core.rls_guard must include every table that
    backs a category-D model. If you add a new RLS table to the app,
    update BOTH the SQL migration AND the guard's frozenset."""
    from app.core.rls_guard import RLS_DML_TABLES

    required_db_tables: set[str] = {
        "portal_knowledge_bases",
        "portal_groups",
        "portal_group_products",
        "portal_group_kb_access",
        "portal_kb_tombstones",
        "portal_user_kb_access",
        "portal_user_products",
        "portal_retrieval_gaps",
        "portal_taxonomy_nodes",
        "portal_taxonomy_proposals",
        "vexa_meetings",
    }
    missing = required_db_tables - RLS_DML_TABLES
    assert not missing, f"RLS_DML_TABLES missing category-D tables: {missing}"
