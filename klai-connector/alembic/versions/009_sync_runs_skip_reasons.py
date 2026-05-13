"""SPEC-INGEST-RECONCILE-001 — add connector.sync_runs.skip_reasons.

Adds a JSONB column ``skip_reasons`` mapping ``{reason_code: count}`` for
docs that were fetched/parsed but NOT persisted as artifacts. Populated by
``sync_engine._execute_sync`` at the end of every sync run.

A CHECK constraint validates that every key is a member of the
:class:`PersistSkipReason` enum (kept in sync via parity test
``klai-connector/tests/test_reason_codes_parity.py``). Adding a new reason
requires:

  1. Append to ``PersistSkipReason`` in both services.
  2. Write a follow-up migration that drops + re-adds this CHECK with the
     new key in the IN (...) list.

This is the mechanical guard against typo-introduced silent reasons that
SPEC §"Fix 3" depends on.

``documents_ok`` arithmetic is corrected in the application layer
(sync_engine), not the schema. Existing column semantics: previously
"submitted to ingest", now "documents_persisted = total - failed -
sum(skip_reasons.values())". No backfill — existing rows keep their
old (over-counted) ``documents_ok`` value and ``skip_reasons = '{}'``.

Revision ID: 009_sync_runs_skip_reasons
Revises: 008_rls_tenant_isolation
Create Date: 2026-05-06
SPEC: SPEC-INGEST-RECONCILE-001 AC-6, AC-7, AC-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "009_sync_runs_skip_reasons"
down_revision: str | None = "008_rls_tenant_isolation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mirror of ``PersistSkipReason`` values — hardcoded as a static SQL
# literal below. Keeping the list visible here (and grep-able by the
# parity test ``klai-connector/tests/test_reason_codes_parity.py``)
# ensures any future enum addition is caught at PR time rather than
# at production INSERT time. The SQL string is intentionally static
# (no f-string composition) so semgrep's ``formatted-sql-query`` /
# ``sqlalchemy-execute-raw-query`` rules don't flag this migration —
# the values are not user input and never could be at this layer,
# but the static form is simpler than a suppression annotation.


def upgrade() -> None:
    op.execute(
        "ALTER TABLE connector.sync_runs "
        "ADD COLUMN IF NOT EXISTS skip_reasons jsonb NOT NULL DEFAULT '{}'::jsonb"
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'sync_runs_skip_reasons_valid_keys'
                  AND conrelid = 'connector.sync_runs'::regclass
            ) THEN
                ALTER TABLE connector.sync_runs
                DROP CONSTRAINT sync_runs_skip_reasons_valid_keys;
            END IF;
        END
        $$;
        """
    )
    # Postgres rejects subqueries inside CHECK constraints
    # ("cannot use subquery in check constraint"). The membership test
    # is expressed via JSONB key-removal: ``skip_reasons - allowed[]``
    # strips every allowed key from the object; if the result is
    # ``'{}'`` then every original key was in the allowed list. Empty
    # input is also ``'{}'`` which trivially passes.
    op.execute(
        """
        ALTER TABLE connector.sync_runs
        ADD CONSTRAINT sync_runs_skip_reasons_valid_keys
        CHECK (
            jsonb_typeof(skip_reasons) = 'object'
            AND (skip_reasons - ARRAY[
                'content_too_short',
                'auth_wall_detected',
                'dedupe_content_hash_match',
                'dedupe_raw_html_hash_match',
                'non_text_content',
                'excluded_by_kb_config',
                'taxonomy_classify_failed'
            ]::text[]) = '{}'::jsonb
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'sync_runs_skip_reasons_valid_keys'
                  AND conrelid = 'connector.sync_runs'::regclass
            ) THEN
                ALTER TABLE connector.sync_runs
                DROP CONSTRAINT sync_runs_skip_reasons_valid_keys;
            END IF;
        END
        $$;
        """
    )
    op.execute("ALTER TABLE connector.sync_runs DROP COLUMN IF EXISTS skip_reasons")
