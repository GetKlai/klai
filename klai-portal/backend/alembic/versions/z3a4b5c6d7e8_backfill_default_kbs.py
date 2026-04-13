"""backfill default org + personal KBs for existing tenants

Revision ID: z3a4b5c6d7e8
Revises: z2a3b4c5d6e7
Create Date: 2026-04-13
"""

from alembic import op

revision = "z3a4b5c6d7e8"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create org KB for every org that doesn't have one yet
    op.execute("""
        INSERT INTO portal_knowledge_bases
            (org_id, name, slug, created_by, visibility, docs_enabled, owner_type, default_org_role)
        SELECT
            o.id,
            'Organisatiekennis',
            'org',
            COALESCE((SELECT u.zitadel_user_id FROM portal_users u WHERE u.org_id = o.id LIMIT 1), 'system'),
            'internal',
            false,
            'org',
            'viewer'
        FROM portal_orgs o
        WHERE NOT EXISTS (
            SELECT 1 FROM portal_knowledge_bases kb
            WHERE kb.org_id = o.id AND kb.slug = 'org'
        )
    """)

    # 2. Create personal-{user_id} KB for every user that doesn't have one yet
    op.execute("""
        INSERT INTO portal_knowledge_bases
            (org_id, name, slug, created_by, visibility, docs_enabled, owner_type, owner_user_id)
        SELECT
            u.org_id,
            'Persoonlijk',
            'personal-' || u.zitadel_user_id,
            u.zitadel_user_id,
            'internal',
            false,
            'user',
            u.zitadel_user_id
        FROM portal_users u
        WHERE NOT EXISTS (
            SELECT 1 FROM portal_knowledge_bases kb
            WHERE kb.org_id = u.org_id AND kb.slug = 'personal-' || u.zitadel_user_id
        )
    """)

    # 3. Clean up legacy: if a KB with slug='personal' exists (from old lazy-create),
    #    rename it to personal-{owner_user_id} if owner_user_id is set,
    #    otherwise delete it (orphaned row with no user association)
    op.execute("""
        UPDATE portal_knowledge_bases
        SET slug = 'personal-' || owner_user_id
        WHERE slug = 'personal'
          AND owner_user_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM portal_knowledge_bases kb2
              WHERE kb2.org_id = portal_knowledge_bases.org_id
                AND kb2.slug = 'personal-' || portal_knowledge_bases.owner_user_id
          )
    """)
    op.execute("""
        DELETE FROM portal_knowledge_bases
        WHERE slug = 'personal' AND owner_user_id IS NULL
    """)


def downgrade() -> None:
    # Best-effort: rename personal-{user_id} back to personal for the first user per org
    # This is lossy for multi-user orgs but acceptable for a dev-phase migration
    op.execute("""
        DELETE FROM portal_knowledge_bases WHERE slug = 'org'
    """)
    op.execute("""
        DELETE FROM portal_knowledge_bases WHERE slug LIKE 'personal-%'
    """)
