"""SPEC-KNOWLEDGE-ACTIVITY-001 test-mark rework — widget_conversations.is_test.

Pure file-content and model checks, same reading style as
tests/test_rls_hygiene.py and tests/test_answer_reviews_schema.py: the DDL for
this klai-owned table lives in post-deploy SQL, so there is no live-PostgreSQL
assertion here.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Boolean

from app.models.retrieval_gaps import PortalRetrievalGap
from app.models.widgets import WidgetConversation

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
IS_TEST_SQL_PATH = VERSIONS_DIR / "post_deploy_2753d8303a76_widget_conversations_is_test.sql"


def test_widget_conversation_model_has_is_test_column() -> None:
    column = WidgetConversation.__table__.columns["is_test"]
    assert isinstance(column.type, Boolean)
    assert column.nullable is False


def test_is_test_post_deploy_sql_is_idempotent_and_transactional() -> None:
    assert IS_TEST_SQL_PATH.exists(), f"missing post-deploy SQL: {IS_TEST_SQL_PATH}"
    sql = IS_TEST_SQL_PATH.read_text(encoding="utf-8")

    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    assert "information_schema.columns" in sql
    assert "table_name = 'widget_conversations' AND column_name = 'is_test'" in sql
    assert "ALTER TABLE widget_conversations ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT false" in sql


def test_retrieval_gaps_resolved_by_check_allows_test() -> None:
    """The 'test' closer (PUT .../conversations/{id}/test) needs its own
    resolved_by value, widened in 6a0a2f1c33d6_widen_retrieval_gaps_resolved_by_test.py."""
    constraint = next(
        c
        for c in PortalRetrievalGap.__table__.constraints
        if getattr(c, "name", None) == "ck_retrieval_gaps_resolved_by"
    )
    assert "'test'" in str(constraint.sqltext)

    migration_path = VERSIONS_DIR / "6a0a2f1c33d6_widen_retrieval_gaps_resolved_by_test.py"
    assert migration_path.exists()
    migration = migration_path.read_text(encoding="utf-8")
    assert "'rescorer', 'review', 'manual', 'test'" in migration
