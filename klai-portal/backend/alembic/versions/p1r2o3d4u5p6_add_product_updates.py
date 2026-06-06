"""add lightweight product updates

Revision ID: p1r2o3d4u5p6
Revises: n2o3p4q5r6s7
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "p1r2o3d4u5p6"
down_revision: Union[str, Sequence[str], None] = "n2o3p4q5r6s7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_updates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "commit_shas",
            postgresql.ARRAY(sa.String(length=40)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_product_updates_title_nonempty"),
        sa.CheckConstraint("length(btrim(body)) > 0", name="ck_product_updates_body_nonempty"),
    )
    op.create_index("ix_product_updates_created", "product_updates", ["created_at"])

    op.create_table(
        "product_update_reads",
        sa.Column(
            "product_update_id",
            sa.BigInteger(),
            sa.ForeignKey("product_updates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_product_update_reads_user", "product_update_reads", ["org_id", "user_id", "read_at"])

    op.execute("GRANT SELECT, INSERT ON product_updates TO portal_api")
    op.execute("GRANT SELECT, INSERT ON product_update_reads TO portal_api")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE product_updates_id_seq TO portal_api")


def downgrade() -> None:
    op.execute("REVOKE USAGE, SELECT ON SEQUENCE product_updates_id_seq FROM portal_api")
    op.execute("REVOKE SELECT, INSERT ON product_update_reads FROM portal_api")
    op.execute("REVOKE SELECT, INSERT ON product_updates FROM portal_api")
    op.drop_index("ix_product_update_reads_user", table_name="product_update_reads")
    op.drop_table("product_update_reads")
    op.drop_index("ix_product_updates_created", table_name="product_updates")
    op.drop_table("product_updates")
