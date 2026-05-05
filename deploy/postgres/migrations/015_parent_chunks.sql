-- Migration: 015_parent_chunks.sql
-- SPEC-RAG-PARENT-CHILD-001 — parent-child chunking storage.
--
-- Children (small ~300 tokens) live in Qdrant for embedding-and-matching.
-- Parents (large ~1500 tokens) live in Postgres and replace the child text
-- in the retrieval response so the LLM sees broader narrative context.
--
-- Each child's Qdrant payload carries a parent_chunk_id pointing here.
-- ON DELETE CASCADE on artifact_id keeps parent_chunks in lockstep with
-- knowledge.artifacts (which itself cascades from connector deletes).

CREATE TABLE IF NOT EXISTS knowledge.parent_chunks (
    id            BIGSERIAL PRIMARY KEY,
    artifact_id   UUID        NOT NULL REFERENCES knowledge.artifacts(id) ON DELETE CASCADE,
    org_id        TEXT        NOT NULL,
    text          TEXT        NOT NULL,
    token_count   INT         NOT NULL,
    position      INT         NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_parent_chunks_artifact
    ON knowledge.parent_chunks (artifact_id);

CREATE INDEX IF NOT EXISTS ix_parent_chunks_org
    ON knowledge.parent_chunks (org_id);

COMMENT ON TABLE knowledge.parent_chunks IS
    'SPEC-RAG-PARENT-CHILD-001: large parent chunks fetched at retrieval time. '
    'Children live in Qdrant; this table stores the broader-context text the '
    'LLM actually sees in its prompt.';
