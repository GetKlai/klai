# SPEC-RAG-INTAKE-QUALITY-001 — Intake answer coverage

Status: evaluation tooling; no production intake behaviour changed.

## Contract

Given real questions with a verified answer article and a literal answer span,
measure separately whether the span fits in one indexed child, whether retrieval
returns an answering child, and whether the returned text contains the span.
A parent returned for a child can answer a question even when that child alone
cannot. These are intake diagnostics, not answer-quality or hallucination scores.

## Reuse

- `knowledge_ingest.eval.intake_quality` uses the existing RAGAS YAML suite loader
  and expected-article canary. No new dependencies, queue, database or suite format.
- Each query has one unique document URL/path in `expected_chunks` and a literal
  `reference_answer`. The private snapshot is keyed by query id, with `source_text`,
  `indexed_chunks` and `retrieved_chunks`. Chunks contain `chunk_id`, `text` and
  article identity in the existing RAGAS fields (`source_url` or `metadata.path`).
  When retrieval omits the path, resolve it through the verified artifact id;
  never copy the expected path onto an unrelated returned chunk.
- `python -m knowledge_ingest.eval.intake_quality --suite PATH --snapshot PATH`
  runs in the knowledge-ingest environment and prints aggregate JSON. It performs
  no network requests or database writes. Missing cases and invalid source spans
  fail the run; an explicitly empty retrieved list means a completed search with
  no passages. Never represent a failed/unperformed search as an empty result.
- `python -m scripts.export_librechat_messages --database DB --since ISO --until ISO`
  runs in the portal environment. It reuses the existing Mongo client and message
  normalizer. Bounds require timezones and are inclusive/exclusive respectively.
  Error and unfinished messages remain in the export. Output is private JSONL.

## Measurement boundary

Raw questions, identifiers, snapshots and per-customer results stay outside the
public repository. Publish only anonymous experimental aggregates. Case selection
and source spans must be fixed before looking at retrieval outcomes. A selected
span measures that fact, not completeness of a multi-part answer or freshness of
the underlying article. The shared canary is substring-based: use unique markers.

The answer-quality gate remains SPEC-RAG-ANSWER-JUDGES-001 evolutie §6: same real
questions, old behaviour alongside, three answer attempts, at least two blind
random-order judging rounds, and no removal of a previously supported answer.
A source repair or question-vector change requires that gate before production
reindexing. Do not widen intake on the strength of a retrieval-only score.
