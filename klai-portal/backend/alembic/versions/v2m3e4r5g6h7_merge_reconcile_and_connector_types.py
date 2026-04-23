"""merge reconcile_legacy_portal_templates + extend_portal_connectors_type

Revision ID: v2m3e4r5g6h7
Revises: u1r2e3c4o5n6, 6e01fa349b6e
Create Date: 2026-04-23

Two independent migrations off the same parent (t3a4b5c6d7e8) landed in
parallel:
- u1r2e3c4o5n6 — SPEC-CHAT-TEMPLATES-CLEANUP-001 reconcile
- 6e01fa349b6e — SPEC-KB-CONNECTORS-001 extend connector-type CHECK

This is a pure alembic DAG merge with no schema changes.
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "v2m3e4r5g6h7"
down_revision: Union[str, Sequence[str], None] = ("u1r2e3c4o5n6", "6e01fa349b6e")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: merge-only migration."""
    pass


def downgrade() -> None:
    """No-op: merge-only migration."""
    pass
