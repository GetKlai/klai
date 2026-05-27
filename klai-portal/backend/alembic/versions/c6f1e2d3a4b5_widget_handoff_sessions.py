"""marker for widget handoff session tables

Revision ID: c6f1e2d3a4b5
Revises: fb2c3d4e5f6a
Create Date: 2026-05-27

The tables reference klai-owned widget audit tables and need RLS policy
setup, so the DDL lives in post_deploy_c6f1e2d3a4b5_widget_handoff_sessions.sql.
"""

from typing import Sequence, Union


revision: str = "c6f1e2d3a4b5"
down_revision: Union[str, Sequence[str], None] = "fb2c3d4e5f6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
