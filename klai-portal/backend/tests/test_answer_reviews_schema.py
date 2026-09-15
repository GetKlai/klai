"""SPEC-KNOWLEDGE-ACTIVITY-001 §4.2/§4.4 — answer_reviews storage + rights (fase 1a).

Pure file-content and registry checks: the DDL lives in post-deploy SQL (the
table is klai-owned, Cat-D RLS), so there is no live-PostgreSQL assertion here.
Same reading style as ``tests/test_rls_hygiene.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.extensions_registry import KNOWN_FEATURES, PRODUCT_FEATURES
from app.core.profiles import PROFILE_CAPABILITIES, Capability
from app.models.answer_reviews import AnswerReview

POST_DEPLOY_SQL_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_c2a7e9d4b1f6_answer_reviews_rls.sql"
)

# Leading keywords of a non-column line inside the CREATE TABLE block. Column
# definitions are one per line; a wrapped CHECK/UNIQUE continuation line starts
# with one of these, so it is never mistaken for a column.
_CONSTRAINT_KEYWORDS = ("CHECK", "CONSTRAINT", "UNIQUE", "PRIMARY", "FOREIGN", "EXCLUDE")


def _read_sql() -> str:
    assert POST_DEPLOY_SQL_PATH.exists(), f"missing post-deploy SQL: {POST_DEPLOY_SQL_PATH}"
    return POST_DEPLOY_SQL_PATH.read_text(encoding="utf-8")


def _create_table_block(sql: str) -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS answer_reviews \((.*?)\n\);", sql, re.DOTALL)
    assert m, "answer_reviews CREATE TABLE IF NOT EXISTS block not found in post-deploy SQL"
    return m.group(1)


def _sql_column_names(block: str) -> set[str]:
    names: set[str] = set()
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        if line.upper().startswith(_CONSTRAINT_KEYWORDS):
            continue
        names.add(line.split()[0].rstrip(","))
    return names


def test_model_columns_match_sql() -> None:
    """ORM column set must be exactly the DDL column set.

    The post-deploy SQL is the source of truth (portal_api cannot run this
    DDL itself); a column added to only one of the two silently desynchronises
    the ORM from the live table.
    """
    sql_columns = _sql_column_names(_create_table_block(_read_sql()))
    model_columns = {column.name for column in AnswerReview.__table__.columns}
    assert model_columns == sql_columns


def test_answer_reviews_sql_has_force_rls_and_policy() -> None:
    """Cat-D shape: ENABLE + FORCE RLS, and one DROP guard per CREATE POLICY."""
    sql = _read_sql()
    assert "ALTER TABLE answer_reviews ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE answer_reviews FORCE ROW LEVEL SECURITY" in sql
    create_count = len(re.findall(r"CREATE POLICY tenant_isolation ON answer_reviews", sql))
    drop_count = len(re.findall(r"DROP POLICY IF EXISTS tenant_isolation ON answer_reviews", sql))
    assert create_count == 1, f"expected exactly one tenant_isolation policy, got {create_count}"
    assert drop_count == create_count, f"{create_count} CREATE POLICY but only {drop_count} DROP IF EXISTS guards"


def test_kb_activity_capability_granted_from_kb_manager() -> None:
    """kb.activity starts at kb_manager — reviewing answers is a knowledge-side
    management task, never a personal/company one."""
    for role in ("kb_manager", "group_manager", "admin"):
        assert Capability.KB_ACTIVITY in PROFILE_CAPABILITIES[role], f"{role} must have kb.activity"
    for role in ("personal", "company"):
        assert Capability.KB_ACTIVITY not in PROFILE_CAPABILITIES[role], f"{role} must not have kb.activity"


def test_knowledge_activity_is_known_feature() -> None:
    """Per-tenant opt-in, writable through platform-unlocks but deliberately NOT
    a user-facing product (no FEATURE_MIN_PROFILE entry)."""
    assert "knowledge_activity" in KNOWN_FEATURES
    assert "knowledge_activity" not in PRODUCT_FEATURES


def test_kb_activity_survives_the_seat_filter():
    """Production derives capabilities through the seat tier, not from
    PROFILE_CAPABILITIES directly: a capability without a seat-feature mapping
    is dropped for everyone, which would 403 the whole activity API."""
    from app.core.seats import SeatType, effective_capabilities

    assert "kb.activity" in effective_capabilities("kb_manager", SeatType.KNOWLEDGE)
    assert "kb.activity" in effective_capabilities("admin", SeatType.KNOWLEDGE)
    assert "kb.activity" not in effective_capabilities("company", SeatType.KNOWLEDGE)
