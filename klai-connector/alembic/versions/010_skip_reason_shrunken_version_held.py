"""Allow ``shrunken_version_held`` in connector.sync_runs.skip_reasons.

knowledge-ingest now holds a connector version that keeps under half of the
stored version's words until the same version arrives again 12 hours later.
The sync counts such a document under this skip reason, so the CHECK that
pins skip_reasons keys to ``PersistSkipReason`` gains the new key
(procedure in 009_sync_runs_skip_reasons.py).

Revision ID: 010_skip_reason_shrunken_version_held
Revises: 009_sync_runs_skip_reasons
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "010_skip_reason_shrunken_version_held"
down_revision: str | None = "009_sync_runs_skip_reasons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP = "ALTER TABLE connector.sync_runs DROP CONSTRAINT IF EXISTS sync_runs_skip_reasons_valid_keys"


def upgrade() -> None:
    op.execute(_DROP)
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
                'taxonomy_classify_failed',
                'shrunken_version_held'
            ]::text[]) = '{}'::jsonb
        )
        """
    )


def downgrade() -> None:
    op.execute(_DROP)
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
