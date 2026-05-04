-- Migration: 014_rag_eval_results.sql
-- SPEC-RAG-EVAL-001 — store nightly RAGAS metrics per query/suite/variant.
-- See .moai/specs/SPEC-RAG-EVAL-001/spec.md

CREATE TABLE IF NOT EXISTS knowledge.rag_eval_results (
  id                BIGSERIAL PRIMARY KEY,
  run_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  suite             TEXT NOT NULL,
  variant           TEXT NOT NULL DEFAULT 'baseline',
  query_id          TEXT NOT NULL,
  context_precision FLOAT,
  context_recall    FLOAT,
  faithfulness      FLOAT,
  answer_relevance  FLOAT,
  retrieved_chunk_ids TEXT[],
  retrieval_ms      INT,
  total_tokens      INT,
  meta              JSONB
);

CREATE INDEX IF NOT EXISTS ix_rag_eval_run_at_suite
    ON knowledge.rag_eval_results (run_at DESC, suite);

CREATE INDEX IF NOT EXISTS ix_rag_eval_variant_run_at
    ON knowledge.rag_eval_results (variant, run_at DESC);
