"""SPEC-PRIVACY-PII-POLICY-ADMIN-001 PR1: portal_orgs.pii_allow_list

Revision ID: 76f43911a5ba
Revises: 5d8cef52b18c
Create Date: 2026-08-21 13:59:01.599877

Adds the per-tenant PII allow-list column (D1's subtractive model): a
tenant excludes specific values, patterns or keywords from what the
platform default would otherwise mask. Storage shape per the SPEC's REQ-3
schema: ``jsonb`` array of ``{"value": str, "match": "exact"|"regex",
"note": str | None}``.

This migration is PURE DDL (metadata-only ``ADD COLUMN ... DEFAULT``, no
UPDATE/INSERT), so it is safe under the Cat-A/portal_orgs Alembic
constraint documented in ``klai-portal/backend/AGENTS.md`` — same
precedent as ``5d8cef52b18c`` (``pii_masked_entities``).

The CHECK constraint is deliberately shallow: it only pins the column's
*shape* (must be a JSON array, at most
``pii_allow_list.MAX_ALLOW_LIST_ENTRIES`` elements) rather than validating
each element's structure in SQL. Per-element validation (value length,
match kind, regex compile-safety) is enforced in Python by
``app.services.pii_allow_list.validate_allow_list``, which every write
path MUST call. A DB-level element-shape CHECK was considered and
rejected: JSONB structural validation in a CHECK constraint is brittle
(no native per-element assertion without a custom SQL function) and the
low-value defense (catching a superuser backfill that skips Python
entirely) is already covered by the array-length + array-type check here.

**Enforcement-side plumbing into Presidio's ``allow_list`` parameter is
NOT part of this migration or this PR** — this only adds storage. See
``app.services.pii_allow_list`` for the write-time validation this column
depends on.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "76f43911a5ba"
down_revision = "5d8cef52b18c"
branch_labels = None
depends_on = None

# The CHECK below hardcodes the number 50, mirroring
# app.services.pii_allow_list.MAX_ALLOW_LIST_ENTRIES. Duplicated rather
# than imported: Alembic migrations must not import application code (a
# later refactor of the constant must not silently change what an
# already-applied migration asserted), and the literal keeps the SQL a
# plain string rather than an f-string built from a "constant" that reads
# like user input to static analysis.


def upgrade() -> None:
    """Add portal_orgs.pii_allow_list + a shape-only domain CHECK."""
    op.execute(
        """
        ALTER TABLE public.portal_orgs
            ADD COLUMN IF NOT EXISTS pii_allow_list jsonb
                NOT NULL DEFAULT '[]'::jsonb;
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
                WHERE conname = 'chk_portal_orgs_pii_allow_list_shape'
                  AND conrelid = 'public.portal_orgs'::regclass
            ) THEN
                ALTER TABLE public.portal_orgs
                    ADD CONSTRAINT chk_portal_orgs_pii_allow_list_shape
                    CHECK (
                        jsonb_typeof(pii_allow_list) = 'array'
                        AND jsonb_array_length(pii_allow_list) <= 50
                    );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Drop the constraint, then the column."""
    op.execute("ALTER TABLE public.portal_orgs DROP CONSTRAINT IF EXISTS chk_portal_orgs_pii_allow_list_shape;")
    op.execute("ALTER TABLE public.portal_orgs DROP COLUMN IF EXISTS pii_allow_list;")
