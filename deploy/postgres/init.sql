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

-- The klai database (portal data, billing, docs) uses the root klai user
