# chunk_type Drop — Root Cause Diagnosis

SPEC-CRAWLER-005 REQ-03.3

## Observed symptom

Voys `support` re-sync via SPEC-CRAWLER-004 delegation path ingested 167 crawl chunks.
Every chunk was missing `chunk_type` in the Qdrant payload.

## Root cause trace

1. `enrich_chunk()` in `knowledge_ingest/enrichment.py` calls LiteLLM and parses the
   response with `EnrichmentResult.model_validate_json(content)`.

2. `EnrichmentResult.chunk_type` is declared as:
   ```python
   chunk_type: Literal["procedural", "conceptual", "reference", "warning", "example"]
   ```

3. When the LLM returns a value outside the Literal set (e.g. `""`, `"factual"`,
   `"general"`, `"overview"`), Pydantic raises `ValidationError`.

4. The `ValidationError` is caught in the same `except` clause as `KeyError`, `IndexError`,
   and `ValueError`, and is re-raised as `EnrichmentError`:
   ```python
   except (KeyError, IndexError, ValidationError, ValueError) as exc:
       raise EnrichmentError(f"Unparseable LLM response for {path}: {exc}") from exc
   ```

5. `EnrichmentError` propagates out of `enrich_chunk()` and `enrich_chunks()`.

6. In `_enrich_document()` (enrichment_tasks.py), `EnrichmentError` is explicitly
   re-raised so Procrastinate retries the job:
   ```python
   except enrichment.EnrichmentError:
       ...
       raise  # Procrastinate retry handles this
   ```

7. The Procrastinate task (`enrich_document_bulk`) has `max_attempts=2`. After both
   attempts fail with `EnrichmentError` due to invalid `chunk_type`, the job enters
   permanent-failed state.

8. The pre-enrichment fast path (`ingest_document()` in `routes/ingest.py`) has already
   written raw chunks to Qdrant via `upsert_chunks()` — without `chunk_type`. The
   enrichment job was meant to overwrite them via `upsert_enriched_chunks()`, but the
   permanent failure means it never runs.

9. Result: 167 chunks in Qdrant, all missing `chunk_type`.

## Why crawl chunks specifically

Crawl documents have diverse, short, navigation-heavy content. The LLM is more likely
to classify these with vague values like `"overview"`, `"navigation"`, or `""` rather
than one of the five prescribed Literal values. KB article chunks tend to be longer
and more structured, making the classification more reliable.

## Gate in qdrant_store (not the root cause)

`upsert_enriched_chunks()` at `qdrant_store.py:275` has:
```python
if getattr(ec, "chunk_type", ""):
    payload["chunk_type"] = ec.chunk_type
```
This silently omits `chunk_type` when `ec.chunk_type == ""`. This is correct defensive
behaviour, but it does not fix the underlying problem — it only prevents writing an
invalid value. The real fix is upstream, in `enrich_chunk()`.

## Fix implemented

Two-step parse approach in `enrich_chunk()` (SPEC-CRAWLER-005 plan.md Fase 3):

1. `json.loads()` first: genuine JSON parse failure (transport problem) → `EnrichmentError`
   (Procrastinate retries — correct behaviour).

2. `model_validate()` second: `chunk_type` validation failure → retry ONCE with a
   strengthened prompt addendum (`_CHUNK_TYPE_RETRY_ADDENDUM`).

3. If retry also returns invalid `chunk_type`: fall back to `chunk_type="reference"` and
   emit a structured `crawl_chunk_type_drop` warning log with `artifact_id`,
   `chunk_index`, and `raw_llm_response[:200]` for ops monitoring (EC-4).

This ensures:
- No `EnrichmentError` is raised for invalid `chunk_type` alone.
- Every chunk gets a valid `chunk_type` in the five-value set.
- Transport failures (bad JSON) still propagate as `EnrichmentError` for Procrastinate retry.
- `artifact_id` is now passed from `_enrich_document()` through `enrich_chunks()` to
  `enrich_chunk()` for log correlation.

## Files changed

- `klai-knowledge-ingest/knowledge_ingest/enrichment.py` — two-step parse, retry, fallback,
  `_call_llm()` + `_extract_content()` helpers, `artifact_id`/`chunk_index` params.
- `klai-knowledge-ingest/knowledge_ingest/enrichment_tasks.py` — pass `artifact_id=artifact_id`
  to `enrich_chunks()`.
- `klai-knowledge-ingest/tests/test_chunk_type_crawl.py` — 6 new TDD tests (AC-03.1, AC-03.2, EC-4).
- `klai-knowledge-ingest/tests/test_enrichment.py` — updated `test_enrich_chunk_chunk_type_validation`
  to reflect retry+fallback behaviour instead of expected `EnrichmentError`.
