"""marker for widget handoff agent names

Revision ID: d7e8f9a0b1c3
Revises: c6f1e2d3a4b5
Create Date: 2026-05-27

The nullable column is added in post-deploy SQL so production can apply it
idempotently alongside the existing handoff tables.
"""

from __future__ import annotations

revision: str = "d7e8f9a0b1c3"
down_revision: str | None = "c6f1e2d3a4b5"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
