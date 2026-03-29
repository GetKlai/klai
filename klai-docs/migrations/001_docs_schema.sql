-- Klai Docs schema
-- Run against the existing klai PostgreSQL database on core-01

CREATE SCHEMA IF NOT EXISTS docs;

-- ─── Organizations ────────────────────────────────────────────────────────────
-- Mirrors Zitadel organisations; created during tenant provisioning.
CREATE TABLE docs.organizations (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            text        UNIQUE NOT NULL,    -- "voys" → voys.getklai.com
    name            text        NOT NULL,
    zitadel_org_id  text        NOT NULL,
    gitea_org_name  text        NOT NULL,           -- "org-voys" in Gitea
    created_at      timestamptz DEFAULT now()
);

-- ─── Knowledge bases ─────────────────────────────────────────────────────────
CREATE TABLE docs.knowledge_bases (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES docs.organizations(id) ON DELETE CASCADE,
    slug        text        NOT NULL,               -- "help-center"
    name        text        NOT NULL,
    visibility  text        NOT NULL DEFAULT 'private'
                            CHECK (visibility IN ('public', 'private')),
    gitea_repo  text        NOT NULL,               -- "org-voys/help-center"
    created_at  timestamptz DEFAULT now(),
    UNIQUE (org_id, slug)
);

-- ─── Custom domains ───────────────────────────────────────────────────────────
-- One custom domain per organization (maps to org root subdomain).
CREATE TABLE docs.custom_domains (
    domain      text        PRIMARY KEY,            -- "docs.voys.nl"
    org_id      uuid        NOT NULL REFERENCES docs.organizations(id) ON DELETE CASCADE,
    verified_at timestamptz,
    created_at  timestamptz DEFAULT now()
);

-- ─── Page edit restrictions ───────────────────────────────────────────────────
-- Absence of a row = all org members can edit (default).
CREATE TABLE docs.page_edit_restrictions (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id       uuid        NOT NULL REFERENCES docs.knowledge_bases(id) ON DELETE CASCADE,
    page_path   text        NOT NULL,               -- "getting-started/quick-start"
    user_ids    text[]      NOT NULL DEFAULT '{}',  -- Zitadel user IDs
    created_at  timestamptz DEFAULT now(),
    UNIQUE (kb_id, page_path)
);

CREATE INDEX ON docs.knowledge_bases (org_id);
CREATE INDEX ON docs.custom_domains (org_id);
CREATE INDEX ON docs.page_edit_restrictions (kb_id);
