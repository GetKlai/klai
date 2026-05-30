#!/usr/bin/env bash
set -euo pipefail

# Ensure the listmonk API user used by portal-api can perform idempotent
# subscriber upserts, duplicate lookup, list membership updates, and
# transactional sends.

LISTMONK_DB_CONTAINER="${LISTMONK_DB_CONTAINER:-klai-core-listmonk-db-1}"
LISTMONK_CONTAINER="${LISTMONK_CONTAINER:-klai-core-listmonk-1}"
LISTMONK_DB_USER="${LISTMONK_DB_USER:-listmonk}"
LISTMONK_DB_NAME="${LISTMONK_DB_NAME:-listmonk}"
LISTMONK_API_USER="${LISTMONK_API_USER:-twenty-crm-sync}"
ROLE_NAME="${LISTMONK_PORTAL_ROLE_NAME:-Portal mailing automation}"

docker exec -i "$LISTMONK_DB_CONTAINER" psql -U "$LISTMONK_DB_USER" -d "$LISTMONK_DB_NAME" \
  -v api_user="$LISTMONK_API_USER" \
  -v role_name="$ROLE_NAME" <<'SQL'
WITH required_permissions AS (
  SELECT ARRAY[
    'lists:get_all',
    'lists:manage_all',
    'subscribers:get',
    'subscribers:get_all',
    'subscribers:manage',
    'subscribers:sql_query',
    'subscribers:import',
    'tx:send'
  ]::text[] AS permissions
),
upsert_role AS (
  INSERT INTO roles (name, permissions)
  SELECT :'role_name', permissions
  FROM required_permissions
  WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :'role_name')
  RETURNING id
),
patched_role AS (
  UPDATE roles
  SET permissions = (
    SELECT array_agg(DISTINCT permission ORDER BY permission)
    FROM unnest(roles.permissions || required_permissions.permissions) AS permission
  )
  FROM required_permissions
  WHERE roles.name = :'role_name'
  RETURNING roles.id
),
portal_role AS (
  SELECT id FROM upsert_role
  UNION
  SELECT id FROM patched_role
)
UPDATE users
SET user_role_id = (SELECT id FROM portal_role LIMIT 1)
WHERE username = :'api_user'
  AND type = 'api';
SQL

# listmonk caches role permissions in-process. Restart after changing roles so
# duplicate-upsert flows can immediately use subscribers:sql_query/lists scopes.
if docker ps --format '{{.Names}}' | grep -qx "$LISTMONK_CONTAINER"; then
  docker restart "$LISTMONK_CONTAINER" >/dev/null
fi

echo "listmonk portal automation role ensured for API user: $LISTMONK_API_USER"
