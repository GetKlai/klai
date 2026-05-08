"""add tenant org_id to scribe transcriptions

Revision ID: 0008_f4a2d9b1
Revises: 0007_c5f9e3a4
Create Date: 2026-05-08 00:00:00.000000

Scribe rows were historically scoped only by Zitadel user id. This adds an
explicit tenant dimension and backfills it from portal membership so every
user-facing transcription query can require both user_id and org_id.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_f4a2d9b1"
down_revision: str | Sequence[str] | None = "0007_c5f9e3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("org_id", sa.VARCHAR(128), nullable=True),
        schema="scribe",
    )

    op.execute(
        """
        UPDATE scribe.transcriptions AS t
        SET org_id = po.zitadel_org_id
        FROM public.portal_users AS pu
        JOIN public.portal_orgs AS po ON po.id = pu.org_id
        WHERE t.user_id = pu.zitadel_user_id
          AND t.org_id IS NULL
        """
    )

    op.execute(
        """
        DO $$
        DECLARE
            missing_count integer;
        BEGIN
            SELECT count(*) INTO missing_count
            FROM scribe.transcriptions
            WHERE org_id IS NULL;

            IF missing_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot backfill org_id for % scribe transcription row(s)',
                    missing_count;
            END IF;
        END $$;
        """
    )

    op.alter_column(
        "transcriptions",
        "org_id",
        existing_type=sa.VARCHAR(128),
        nullable=False,
        schema="scribe",
    )
    op.create_index(
        "ix_scribe_transcriptions_org_user",
        "transcriptions",
        ["org_id", "user_id"],
        schema="scribe",
    )
    op.create_index(
        "ix_scribe_transcriptions_org_created_at",
        "transcriptions",
        ["org_id", "created_at"],
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scribe_transcriptions_org_created_at",
        table_name="transcriptions",
        schema="scribe",
    )
    op.drop_index(
        "ix_scribe_transcriptions_org_user",
        table_name="transcriptions",
        schema="scribe",
    )
    op.drop_column("transcriptions", "org_id", schema="scribe")
