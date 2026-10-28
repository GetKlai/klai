-- Create databases and users on first startup
-- Passwords are set via environment variables in docker-compose.yml

CREATE DATABASE zitadel;
CREATE USER zitadel WITH PASSWORD 'PLACEHOLDER_CHANGE_ME';
GRANT ALL PRIVILEGES ON DATABASE zitadel TO zitadel;

CREATE DATABASE litellm;
CREATE USER litellm WITH PASSWORD 'PLACEHOLDER_CHANGE_ME';
GRANT ALL PRIVILEGES ON DATABASE litellm TO litellm;
\c litellm
GRANT ALL ON SCHEMA public TO litellm;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO litellm;

CREATE DATABASE glitchtip;
CREATE USER glitchtip WITH PASSWORD 'PLACEHOLDER_CHANGE_ME';
GRANT ALL PRIVILEGES ON DATABASE glitchtip TO glitchtip;
\c glitchtip
GRANT ALL ON SCHEMA public TO glitchtip;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO glitchtip;

CREATE DATABASE gitea;
CREATE USER gitea WITH PASSWORD 'PLACEHOLDER_CHANGE_ME';
GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea;
\c gitea
GRANT ALL ON SCHEMA public TO gitea;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO gitea;

CREATE DATABASE vexa;
CREATE USER vexa WITH PASSWORD 'PLACEHOLDER_CHANGE_ME';
GRANT ALL PRIVILEGES ON DATABASE vexa TO vexa;
\c vexa
GRANT ALL ON SCHEMA public TO vexa;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO vexa;
-- Note: after first startup of vexa-meeting-api, seed the system user:
-- INSERT INTO users (id, email, name, max_concurrent_bots, data)
-- VALUES (0, 'system@klai.internal', 'Klai System', 999, '{}') ON CONFLICT DO NOTHING;

-- klai-slack (SPEC-SLACK-001): own database and role, owner of its schema so
-- Alembic can create tables. init.sql only runs on an empty data volume; on the
-- existing production volume create the role and database by hand, see
-- docs/runbooks/slack-integration.md. The real password comes from
-- KLAI_SLACK_DB_PASSWORD (klai-infra/core-01/klai-slack/.env.sops).
CREATE USER klai_slack WITH PASSWORD 'PLACEHOLDER_CHANGE_ME';
CREATE DATABASE klai_slack OWNER klai_slack;

-- The klai database (portal data, billing, docs) uses the root klai user
