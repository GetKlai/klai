-- SPEC-PORTAL-PRICING-PER-USER-001 Phase 1 post-deploy SQL.
--
-- Applied as the ``klai`` superuser AFTER ``alembic upgrade f66c546c12eb``
-- completes successfully. Several pieces of this migration cannot run as
-- portal_api in an alembic transaction:
--
--   1. ``UPDATE portal_users SET seat_type = ...`` — portal_users has
--      FORCE RLS with a Cat-A inline-NULLIF policy whose WITH CHECK
--      clause requires ``app.current_org_id`` to match each row's
--      ``org_id``. Migrations run without tenant context -> every WITH
--      CHECK predicate evaluates to NULL -> all rows rejected -> ``new
--      row violates row-level security policy``. See
--      ``rls-with-check-blocks-migration-update`` pitfall.
--
--   2. ``INSERT INTO portal_user_seat_history`` — depends on the
--      seat_type backfill above being correct.
--
--   3. ``CREATE TRIGGER portal_users_seat_history`` — must be installed
--      AFTER the history backfill INSERT, otherwise the trigger fires
--      on the backfill UPDATE and writes spurious history rows.
--
--   4. ``ALTER TABLE portal_user_seat_history ENABLE / FORCE ROW LEVEL
--      SECURITY`` — same class as ``alembic-cannot-drop-non-
--      portal_api-tables``: needs table-owner privileges that alembic
--      role doesn't have.
--
-- Idempotent: every statement uses IF NOT EXISTS / DROP IF EXISTS /
-- ON CONFLICT / WHERE guards so re-running on a partially-applied
-- database is safe.
--
-- Run from core-01 once after the deploy lands:
--     ./scripts/apply_post_deploy_sql.sh post_deploy_f66c546c12eb.sql
-- or manually:
--     docker exec -i klai-core-postgres-1 sh -c \
--         'psql -U $POSTGRES_USER -d $POSTGRES_DB -v ON_ERROR_STOP=1' \
--         < post_deploy_f66c546c12eb.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Backfill seat_type for KMs / group-managers / admins.
--
--    The alembic ``ADD COLUMN seat_type ... DEFAULT 'chat'`` set every
--    existing row to 'chat'. This UPDATE bumps the role-driven
--    knowledge-tier users to their correct seat. Idempotent via the
--    WHERE seat_type='chat' guard — re-running it is a no-op once the
--    target rows are already on 'knowledge'.
-- ---------------------------------------------------------------------------

UPDATE portal_users
   SET seat_type = 'knowledge'
 WHERE role IN ('kb_manager', 'group_manager', 'admin')
   AND seat_type = 'chat';

-- ---------------------------------------------------------------------------
-- 2. Backfill history: one row per existing user reflecting the FINAL
--    seat_type (post-UPDATE above). Idempotent via NOT EXISTS — the
--    partial-unique index on (user_id) WHERE valid_to IS NULL would
--    refuse a second open row anyway, but the explicit predicate is
--    clearer in the audit log of post-deploy applications.
-- ---------------------------------------------------------------------------

INSERT INTO portal_user_seat_history
    (user_id, org_id, seat_type, role, status, valid_from, change_reason)
SELECT u.id, u.org_id, u.seat_type, u.role::text, u.status::text, u.created_at, 'backfill'
  FROM portal_users u
 WHERE NOT EXISTS (
     SELECT 1 FROM portal_user_seat_history h
      WHERE h.user_id = u.id AND h.valid_to IS NULL
 );

-- ---------------------------------------------------------------------------
-- 3. Install the trigger function. Owner adjusted to portal_api so
--    future alembic-managed CREATE OR REPLACE FUNCTION (e.g. Phase 2's
--    changed_by propagation) works without needing klai superuser again.
-- ---------------------------------------------------------------------------

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
    -- UPDATE path: only fire when an audited column changed.
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
ALTER FUNCTION portal_users_seat_history_trg() OWNER TO portal_api;

-- ---------------------------------------------------------------------------
-- 4. Install the trigger AFTER backfill (step 2 above) so the backfill
--    INSERT does not trigger itself.
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS portal_users_seat_history ON portal_users;
CREATE TRIGGER portal_users_seat_history
    AFTER INSERT OR UPDATE ON portal_users
    FOR EACH ROW EXECUTE FUNCTION portal_users_seat_history_trg();

-- ---------------------------------------------------------------------------
-- 5. Schema-qualified RLS helper for the billing domain.
--
--    Distinct from portal-api's public._rls_current_org_id() (integer)
--    AND distinct from knowledge._rls_current_org_id() (text).
--    Schema-qualification is the prescribed fix in the postgres-no-
--    return-type-overload pitfall — Postgres does NOT support function
--    overloading by return type alone.
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS billing;

CREATE OR REPLACE FUNCTION billing._rls_current_org_id() RETURNS integer AS $$
    SELECT NULLIF(current_setting('app.current_org_id', true), '')::integer;
$$ LANGUAGE sql STABLE;

-- Explicit grants for portal_api. Postgres' default ACL grants USAGE on
-- new schemas and EXECUTE on new functions to PUBLIC; this makes
-- portal_api's read path immune to a future cluster-wide ``REVOKE
-- EXECUTE ON ALL FUNCTIONS ... FROM PUBLIC`` hardening pass.
GRANT USAGE ON SCHEMA billing TO portal_api;
GRANT EXECUTE ON FUNCTION billing._rls_current_org_id() TO portal_api;

-- ---------------------------------------------------------------------------
-- 6. ENABLE + FORCE RLS on portal_user_seat_history.
--
--    ENABLE = policies apply to non-owner sessions.
--    FORCE  = policies apply to the table owner too (portal_api). Without
--             FORCE, portal_api would bypass tenant isolation.
-- ---------------------------------------------------------------------------

ALTER TABLE portal_user_seat_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_user_seat_history FORCE  ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 7. The tenant_isolation policy. Cat-D (strict): no permissive null
--    branch. Callers must SET LOCAL app.current_org_id = T before any
--    SELECT/INSERT/UPDATE/DELETE.
--
--    CREATE POLICY does NOT support IF NOT EXISTS, so we DROP first.
-- ---------------------------------------------------------------------------

DROP POLICY IF EXISTS tenant_isolation ON portal_user_seat_history;

CREATE POLICY tenant_isolation ON portal_user_seat_history
    USING      (org_id = billing._rls_current_org_id())
    WITH CHECK (org_id = billing._rls_current_org_id());

COMMIT;
