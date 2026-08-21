"""SPEC-PRIVACY-MISTRAL-PII-001 REQ-7: portal_orgs.pii_masked_entities

Revision ID: 5d8cef52b18c
Revises: 9bf37c021a4e
Create Date: 2026-08-21

Adds the per-org opt-in set of REQ-7 return-set entity types, read by
``GET /internal/v1/orgs/{org_id}/pii-entities`` and consumed by
``deploy/litellm/klai_pii_org_policy.py``.

Storage shape follows ``portal_orgs.platform_unlocked_features``: a bounded set
of opted-in string flags is a ``text[]`` column on ``portal_orgs``, not a side
table. ``portal_orgs`` is the tenant root and carries no RLS policy of its own,
so this adds no new RLS surface — see the endpoint docstring in
``app/api/internal.py`` for the full reasoning.

``ADD COLUMN ... NOT NULL DEFAULT '{}'`` is metadata-only on PostgreSQL >= 11
(no table rewrite, no per-row UPDATE), so every pre-existing org gets REQ-7's
"per-org, default off" for free. This migration contains pure DDL: no UPDATE,
no INSERT, so it cannot trip a WITH CHECK policy during upgrade.

``portal_orgs`` is portal_api-owned, so this DDL runs in ``upgrade()`` rather
than in a post-deploy superuser script — same precedent as migration
``45b528904319`` (``chk_portal_orgs_slug_safe``).

The CHECK constraint is the server-side backstop for REQ-6/REQ-9 validation:
``SECRET`` and ``NL_BSN`` are unconditional and therefore not per-org settable,
``PERSON`` has no deployed detector, and anything outside REQ-7's return set is
not a known entity type. Array-containment (``<@``) rejects all three classes in
one expression, and rejects a NULL element too (``NULL`` is contained in
nothing). The Python-side equivalent, which any write path must call, is
``app.services.pii_entity_policy.validate_entity_selection`` — keep the two in
step if REQ-7's set ever changes.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "5d8cef52b18c"
down_revision = "9bf37c021a4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add portal_orgs.pii_masked_entities + its REQ-7 domain CHECK."""
    op.execute(
        """
        ALTER TABLE public.portal_orgs
            ADD COLUMN IF NOT EXISTS pii_masked_entities text[]
                NOT NULL DEFAULT '{}'::text[];
        """
    )

    # Idempotent via DO-block guard: PostgreSQL has no ADD CONSTRAINT IF NOT
    # EXISTS, and a manual pre-deploy DDL run must not block `upgrade head`.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'chk_portal_orgs_pii_masked_entities'
                  AND conrelid = 'public.portal_orgs'::regclass
            ) THEN
                ALTER TABLE public.portal_orgs
                    ADD CONSTRAINT chk_portal_orgs_pii_masked_entities
                    CHECK (
                        -- Array containment checks ELEMENTS, not shape: a
                        -- nested array whose leaves are all allowed values
                        -- satisfies `<@` and then reads back as sub-lists the
                        -- Python sanitizer drops, silently disabling the
                        -- policy. Pin the shape to empty (ndims IS NULL) or
                        -- one-dimensional first.
                        (
                            array_ndims(pii_masked_entities) IS NULL
                            OR array_ndims(pii_masked_entities) = 1
                        )
                        AND pii_masked_entities <@ ARRAY[
                            'IBAN_CODE',
                            'CREDIT_CARD',
                            'EMAIL_ADDRESS',
                            'PHONE_NUMBER',
                            'NL_KVK',
                            'NL_BTW',
                            'NL_POSTCODE'
                        ]::text[]
                    );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Drop the constraint, then the column."""
    op.execute("ALTER TABLE public.portal_orgs DROP CONSTRAINT IF EXISTS chk_portal_orgs_pii_masked_entities;")
    op.execute("ALTER TABLE public.portal_orgs DROP COLUMN IF EXISTS pii_masked_entities;")
