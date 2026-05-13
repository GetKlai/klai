-- Klai local development: create additional databases + roles
-- The main 'klai' database is created automatically by POSTGRES_DB env var.

-- LiteLLM database
CREATE DATABASE litellm;
CREATE USER litellm WITH PASSWORD 'litellm-dev';
GRANT ALL PRIVILEGES ON DATABASE litellm TO litellm;
\c litellm
GRANT ALL ON SCHEMA public TO litellm;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO litellm;

-- Switch back to the klai database for application-role creation.
\c klai

-- Application role referenced by RLS policies and GRANT statements in
-- klai-portal/backend/alembic/versions/*. In production this role is created
-- via klai-infra provisioning; in local dev we connect as the 'klai' superuser
-- (which bypasses RLS as the table owner), but the role still needs to EXIST
-- so that policy clauses like `FOR DELETE TO portal_api` and GRANTs parse.
-- NOLOGIN keeps it inert — no one ever connects as portal_api locally.
CREATE ROLE portal_api NOLOGIN;

-- Read-only role referenced by post_deploy_g5h6i7j8k9l0.sql for Grafana metric
-- queries against product_events. In production this role is granted to the
-- Grafana datasource user; locally we just need it to exist so GRANT statements
-- parse. NOLOGIN — no one connects as grafana_reader locally.
CREATE ROLE grafana_reader NOLOGIN;
