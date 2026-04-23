"""merge main heads before templates (SPEC-CHAT-TEMPLATES-001)

Revision ID: t1e2m3p4l5s1
Revises: c160d2b9d885, a2b3c4d5e6f7, b4c5d6e7f8g9, b5c6d7e8f9a0, c4d5e6f7a8b9, 32fc0ed3581b
Create Date: 2026-04-23

Unifies the six open alembic heads on `main` into a single head so the
SPEC-CHAT-TEMPLATES-001 migrations (add_portal_templates + active_template_ids)
can chain linearly. No schema changes — alembic graph-only merge, identical
pattern to `aa7531c292e4_merge_dev_heads.py`.

Heads merged:
- c160d2b9d885  (add_user_kb_preference)
- a2b3c4d5e6f7  (add_github_username_to_portal_users)
- b4c5d6e7f8g9  (add_portal_connectors)
- b5c6d7e8f9a0  (add_librechat_user_id_to_portal_users)
- c4d5e6f7a8b9  (add_display_info_to_portal_users)
- 32fc0ed3581b  (add_provisioning_state_machine_constraint)
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "t1e2m3p4l5s1"
down_revision: Union[str, Sequence[str], None] = (
    "c160d2b9d885",
    "a2b3c4d5e6f7",
    "b4c5d6e7f8g9",
    "b5c6d7e8f9a0",
    "c4d5e6f7a8b9",
    "32fc0ed3581b",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: merge-only migration."""
    pass


def downgrade() -> None:
    """No-op: merge-only migration."""
    pass
