-- Klai local development: create additional databases
-- The main 'klai' database is created automatically by POSTGRES_DB env var.

-- LiteLLM database
CREATE DATABASE litellm;
CREATE USER litellm WITH PASSWORD 'litellm-dev';
GRANT ALL PRIVILEGES ON DATABASE litellm TO litellm;
\c litellm
GRANT ALL ON SCHEMA public TO litellm;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO litellm;
