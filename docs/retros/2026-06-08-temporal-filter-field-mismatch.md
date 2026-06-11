# Temporal retrieval filter is inert — field-name mismatch + no supersession payload update

> Found 2026-06-08 during the `docs/architecture` doc-vs-code drift audit.
> Status: ~~unconfirmed in production~~ → **RESOLVED on the serving path, 2026-06-11**
> (see Status addendum below). Backlog handle: `GAP-TEMPORAL-01` in
> `docs/architecture/product-gaps-backlog.md` (now marked fixed).

## Status addendum — 2026-06-11 (verified against source)

Both halves of this retro are now answered:

1. **The field-name mismatch is fixed.** Retrieval uses a dual-contract filter
   (`search.py::_temporal_validity_filter`): `must_not` over the legacy
   `valid_at`/`invalid_at` ISO fields **and** the ingest-written
   `valid_from`/`valid_until` epoch fields, with the open-ended sentinel
   (`belief_time_end = 253402300800`) treated as active. Integration-tested in
   `klai-retrieval-api/tests/test_search.py` (expired / future-valid / active /
   legacy-timeless cases, against a real Qdrant).
2. **The open question is answered: stale chunks do NOT linger on re-ingest.**
   The delete lives *inside* the upsert functions, not in the route: both
   `qdrant_store.upsert_chunks` and `qdrant_store.upsert_enriched_chunks` start
   with `client.delete(...)` on `(org_id, kb_slug, path)` before upserting
   (qdrant_store.py, "Delete existing points for this document"). The random
   `uuid4` point IDs flagged below are therefore harmless — overwrite-by-ID is
   not the mechanism; delete-by-path-filter is. Page deletes call
   `delete_document()`. The original analysis looked for a delete in
   `routes/ingest.py` and missed the one inside the store layer.

So the outcome matches this retro's second branch ("if every route deletes, the
filter is dead-but-harmless defense-in-depth") — except the filter is no longer
dead: it now matches the written fields, making it *working* defense-in-depth
for the one case physical deletion cannot cover (a Qdrant delete that fails
after the PG commit — that residual is the dual-store consistency gap,
`GAP-SYNC-01`). Remaining loose ends, tracked in
`docs/architecture/knowledge-rag-improvement-plan.md` (theme A1):
`soft_delete_artifact` still updates PG only (acceptable given delete-then-upsert,
but `superseded_by` is never set), the recommended end-to-end
ingest→supersede→retrieve test does not exist yet, and `valid_from`/`valid_until`
have no payload index.

---

*Original retro below, kept verbatim for the record.*

## What is certain (verified against source 2026-06-08)

The bitemporal "show only currently-believed knowledge" exclusion at retrieval
time does nothing. Two independent reasons:

1. **Field-name mismatch between ingest and retrieval.**
   - Ingest writes the belief window to the Qdrant payload as `valid_from` /
     `valid_until` — `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:294-296`,
     allow-listed at `:436-437`.
   - Retrieval's only temporal filter, `_invalid_at_filter()`, builds a
     `must_not` range on a field called **`invalid_at`** —
     `klai-retrieval-api/retrieval_api/services/search.py:51-67`, applied to
     every search at `:192` and `:333`.
   - Ingest never writes `invalid_at` anywhere (0 occurrences across
     `klai-knowledge-ingest`). Retrieval never reads `valid_until` /
     `belief_time_end` anywhere (0 occurrences across `retrieval_api`).
   - Net: the `must_not [invalid_at <= now]` clause can never match, because no
     chunk carries `invalid_at`. The filter excludes nothing, on every query.

2. **Supersession never marks existing Qdrant chunks as expired.**
   Even if the field names matched, the exclusion still could not fire on
   in-place supersession. Supersession is a Postgres-only operation:
   `pg_store.soft_delete_artifact()` sets `belief_time_end = now` on the PG
   artifact row (`pg_store.py:231-248`). Nothing issues a Qdrant `set_payload`
   to stamp `valid_until` / `invalid_at` onto the already-stored chunks of the
   superseded artifact. `valid_until` is written once, at ingest time, to the
   active sentinel — and never updated when the artifact is later superseded.

So the temporal model is populated at write time but is not wired end-to-end:
neither the field retrieval reads (`invalid_at`) nor the field ingest writes
(`valid_until`) supports excluding a superseded-in-place chunk.

## Open question (governs real impact — needs tracing before fix)

Whether this causes **stale knowledge to be served to users** depends entirely
on whether every supersession path *also physically deletes* the old Qdrant
points. Two cases are confirmed to delete:

- Connector / crawl reconcile deletes stale chunks by path before/while
  retiring the artifact — `adapters/crawler.py:595` (`delete_document`) +
  `pg_store.soft_delete_stale_connector_artifacts` at `:608`.
- Explicit document/KB delete — `routes/ingest.py:948` (`delete_document`),
  `:1039` (`delete_kb`).

But two facts make in-place re-ingest suspicious and worth tracing:

- `upsert_chunks` uses **random** point IDs (`id=str(uuid.uuid4())`,
  `qdrant_store.py:233,345`), so a re-ingest does **not** overwrite prior
  points by ID.
- The main ingest write path (`routes/ingest.py:644 upsert_chunks`) calls
  `soft_delete_artifact` (PG, `:498`) but has **no** preceding
  `delete_document(path)` in the same path that the crawler path has.

If there is any supersession route that sets `belief_time_end` without a
corresponding Qdrant delete (and is not caught by content-hash dedup), the old
chunks remain searchable and the dead temporal filter is the only thing that was
supposed to hide them — i.e. a real stale-serving exposure. If every route
deletes, the filter is dead-but-harmless defense-in-depth. **This is the fact to
establish first.**

## Recommended fix (after the open question is answered)

- **If stale chunks can linger:** make supersession update the Qdrant payload —
  on `soft_delete_artifact`, `set_payload({"invalid_at": <belief_time_end iso>})`
  (or `valid_until`) on the artifact's chunks — and align the retrieval filter to
  the same field name. Pick one field name and make it the single contract owned
  by one module (cf. `process-rules.md#url-shape-multi-file-drift`: a
  serialization contract split across two services drifts silently).
- **If every supersession route already deletes Qdrant chunks:** then the
  `_invalid_at_filter` is dead code guarding a case that cannot occur. Either
  delete it (and the `valid_from`/`valid_until` writes if nothing else reads
  them) to remove the false sense of a temporal-validity layer, or finish wiring
  it deliberately.
- Either way: add an integration test that ingests an artifact, supersedes it,
  and asserts the superseded chunk is not returned by `/retrieve` — the only test
  that exercises this end-to-end. Today no such test exists (the unit tests inject
  `invalid_at` by hand and never go through the supersession path).

## Why it stayed invisible

The filter is "safe-by-absence": `must_not range` on an absent field passes
through (the docstring at `search.py:54-57` even explains this for the
Qdrant 1.17+ case). So it never errors, never logs, and silently includes
everything — exactly the failure mode of a guard that quietly does nothing.
There is no metric on "chunks excluded by temporal filter" that would have shown
a flat zero.
