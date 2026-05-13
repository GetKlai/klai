"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 2 — trigger reads klai.changed_by_user_id.

The Phase 1 trigger wrote ``changed_by = NULL`` on every history row
because the actor-identity GUC didn't exist yet. Phase 2 introduces it:

  * Portal-api's authenticated dependency (``_resolve_caller_with_options``
    in ``app/core/permissions.py``) calls
        SELECT set_config('klai.changed_by_user_id', '<zitadel_user_id>', false)
    on the pinned connection right after ``set_tenant``.
  * The trigger reads it via ``current_setting('klai.changed_by_user_id', true)``
    and stores into ``changed_by``. ``true`` = missing-OK; the GUC is empty
    for system writes (signup flow, internal endpoints, cron jobs) and the
    column stays NULL — that's the right semantics ("no acting admin to
    attribute this to").

This migration is a single ``CREATE OR REPLACE FUNCTION`` on
``portal_users_seat_history_trg()``. The hotfix transferred ownership of
the function to ``portal_api``, so this runs cleanly inside the standard
alembic upgrade — no klai-superuser path needed.

Idempotent: CREATE OR REPLACE FUNCTION is the canonical idempotent
shape. The downgrade restores the Phase 1 body (NULL changed_by).

Revision ID: 924465b9e0a6
Revises: f66c546c12eb
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import op

revision = "924465b9e0a6"
down_revision = "f66c546c12eb"
branch_labels = None
depends_on = None


# Phase 2 body — same shape as the Phase 1 trigger, plus changed_by
# sourced from current_setting('klai.changed_by_user_id', true).
_TRIGGER_BODY_PHASE2 = """
CREATE OR REPLACE FUNCTION portal_users_seat_history_trg() RETURNS TRIGGER AS $$
DECLARE
    actor TEXT := NULLIF(current_setting('klai.changed_by_user_id', true), '');
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, changed_by, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(), actor, 'invite');
        RETURN NEW;
    END IF;
    -- UPDATE path: only fire when an audited column changed.
    IF (NEW.seat_type IS DISTINCT FROM OLD.seat_type)
       OR (NEW.role     IS DISTINCT FROM OLD.role)
       OR (NEW.status   IS DISTINCT FROM OLD.status) THEN
        UPDATE portal_user_seat_history
           SET valid_to = NOW()
         WHERE user_id = NEW.id
           AND valid_to IS NULL;
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, changed_by, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(), actor,
             CASE
                 WHEN NEW.seat_type IS DISTINCT FROM OLD.seat_type THEN 'seat_change'
                 WHEN NEW.role      IS DISTINCT FROM OLD.role      THEN 'role_change'
                 ELSE 'status_change'
             END);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


# Phase 1 body (no changed_by). Restored on downgrade.
_TRIGGER_BODY_PHASE1 = """
CREATE OR REPLACE FUNCTION portal_users_seat_history_trg() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(), 'invite');
        RETURN NEW;
    END IF;
    IF (NEW.seat_type IS DISTINCT FROM OLD.seat_type)
       OR (NEW.role     IS DISTINCT FROM OLD.role)
       OR (NEW.status   IS DISTINCT FROM OLD.status) THEN
        UPDATE portal_user_seat_history
           SET valid_to = NOW()
         WHERE user_id = NEW.id
           AND valid_to IS NULL;
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(),
             CASE
                 WHEN NEW.seat_type IS DISTINCT FROM OLD.seat_type THEN 'seat_change'
                 WHEN NEW.role      IS DISTINCT FROM OLD.role      THEN 'role_change'
                 ELSE 'status_change'
             END);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_TRIGGER_BODY_PHASE2)


def downgrade() -> None:
    op.execute(_TRIGGER_BODY_PHASE1)
