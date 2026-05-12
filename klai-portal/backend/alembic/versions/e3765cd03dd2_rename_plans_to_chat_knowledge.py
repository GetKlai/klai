"""SPEC-PORTAL-PLAN-RENAME-001 — collapse 4-tier plan ladder to 2-tier.

Renames ``portal_orgs.plan`` values to align with the live marketing model:

    Old slug      | New slug   | Marketing label
    --------------|------------|---------------------------------
    free          | free       | (internal sentinel, unchanged)
    core          | chat       | "Klai Chat"            (€28/mo)
    professional  | chat       | (merged into chat)
    complete      | knowledge  | "Klai Chat + Knowledge" (€68/mo)

Adds a CHECK constraint to lock the column to the new value set so any
future reintroduction of a legacy slug fails-loud at INSERT time rather
than silently falling back to the most-restrictive default in
``get_plan_limits``.

Also updates the ``server_default`` from ``professional`` to ``chat``
(the new-org default).

Migration is idempotent: re-running on an already-migrated DB leaves
values untouched (UPDATE ... WHERE plan IN (...) matches zero rows).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e3765cd03dd2"
down_revision = "85e5d0a7cb98"
branch_labels = None
depends_on = None


# Allowed plan values after this migration. Mirrors PLAN_LIMITS keys in
# app/core/plan_limits.py. Keep in sync if a new plan is ever added.
ALLOWED_PLANS = ("free", "chat", "knowledge")


def upgrade() -> None:
    # Backfill: collapse legacy 4-tier values into the new 2-tier set.
    # Order matters: do the data update BEFORE adding the CHECK constraint,
    # otherwise Postgres would refuse to add the constraint on a table
    # whose existing rows violate it.
    op.execute(
        """
        UPDATE portal_orgs
        SET plan = CASE plan
            WHEN 'core' THEN 'chat'
            WHEN 'professional' THEN 'chat'
            WHEN 'complete' THEN 'knowledge'
            ELSE plan
        END
        WHERE plan IN ('core', 'professional', 'complete')
        """
    )

    # Update the server-side default from "professional" to "chat".
    # ``server_default=`` only affects fresh INSERTs that omit the column;
    # existing rows are unaffected (they were just backfilled above).
    op.execute(
        """
        ALTER TABLE portal_orgs
        ALTER COLUMN plan SET DEFAULT 'chat'
        """
    )

    # CHECK constraint locks the column to the canonical set. A future
    # accidental INSERT/UPDATE of a legacy slug now raises ERRCODE 23514
    # at the DB level instead of silently surfacing as a 500 in the app.
    op.execute(
        """
        ALTER TABLE portal_orgs
        ADD CONSTRAINT portal_orgs_plan_check
        CHECK (plan IN ('free', 'chat', 'knowledge'))
        """
    )


def downgrade() -> None:
    # Drop the CHECK constraint first so the reverse-backfill can run.
    op.execute(
        """
        ALTER TABLE portal_orgs
        DROP CONSTRAINT IF EXISTS portal_orgs_plan_check
        """
    )

    # Reverse the slug rename. Note: we cannot un-merge "professional"
    # from "chat" — both former tiers now sit under "chat" and the
    # original distinction is lost. Picking "core" as the safer downgrade
    # target (lower-tier of the two original collapsed values) so we do
    # not silently auto-grant scribe via the legacy SCRIBE_PLANS check
    # (which was {"professional", "complete"}). Operators who need to
    # split the orgs back into core/professional must do it manually.
    op.execute(
        """
        UPDATE portal_orgs
        SET plan = CASE plan
            WHEN 'chat' THEN 'core'
            WHEN 'knowledge' THEN 'complete'
            ELSE plan
        END
        WHERE plan IN ('chat', 'knowledge')
        """
    )

    op.execute(
        """
        ALTER TABLE portal_orgs
        ALTER COLUMN plan SET DEFAULT 'professional'
        """
    )
