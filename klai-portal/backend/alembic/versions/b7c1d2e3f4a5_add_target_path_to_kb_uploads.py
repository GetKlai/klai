"""kb_uploads.target_path marker (replace-a-source support).

The DDL itself runs as the klai superuser in
``post_deploy_b7c1d2e3f4a5_kb_uploads_target_path.sql`` because
``kb_uploads`` is klai-owned: ``post_deploy_85e5d0a7cb98_kb_uploads_rls.sql``
does ``ALTER TABLE public.kb_uploads OWNER TO klai`` so it can FORCE ROW
LEVEL SECURITY, and FORCE RLS requires ownership. portal_api — the role
alembic runs as — therefore cannot ALTER TABLE it. Putting the ADD COLUMN
in ``upgrade()`` aborts the container at its entrypoint with
``must be owner of table kb_uploads`` (observed on deploy of 3126c181d).

See ``.claude/rules/klai/projects/portal-security.md`` — owner-only DDL on
a FORCE-RLS table belongs in post_deploy SQL, application-role-safe DDL
only in ``upgrade()``.

This file exists only so alembic can advance its head past 76f43911a5ba.

Revision ID: b7c1d2e3f4a5
Revises: 76f43911a5ba
Create Date: 2026-09-02
"""

from __future__ import annotations

revision = "b7c1d2e3f4a5"
down_revision = "76f43911a5ba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling
    post_deploy SQL by the klai superuser.
    """
    # Intentionally empty: see post_deploy_b7c1d2e3f4a5_kb_uploads_target_path.sql


def downgrade() -> None:
    """No-op marker. The schema rollback is applied via SQL as klai superuser."""
