-- Create a read-only Grafana user for the portal database.
-- Run this once on klai-core-postgres-1 as a superuser.
-- Store the generated password in .env as GRAFANA_POSTGRES_PASSWORD.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'grafana_reader') THEN
    -- Replace <generated_password> with the value from SOPS / .env
    CREATE USER grafana_reader WITH PASSWORD '<generated_password>';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE portal TO grafana_reader;
GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;
