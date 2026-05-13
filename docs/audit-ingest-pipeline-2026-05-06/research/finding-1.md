# Finding 1 research: AlreadyEnqueued race in direct-POST flow

## Code verification

**Confirmed — the bug is real. All four sub-claims hold.**

### Claim 1: document_text is frozen in task args

Verified in `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` lines 545-561:

```python
await task_fn.configure(
    queueing_lock=f"{req.org_id}:{req.kb_slug}:{req.path}",
).defer_async(
    org_id=req.org_id,
    kb_slug=req.kb_slug,
    path=req.path,
    document_text=req.content,   # <-- frozen at enqueue time
    chunks=texts,                 # <-- frozen at enqueue time
    ...
)
```

The content of the request body (`req.content`) is serialized into the Procrastinate job row at `defer_async` call time. Procrastinate stores all arguments as JSONB in the `procrastinate_jobs` table. The worker deserializes them from that JSONB when it picks up the job — it does not go back to the source.

### Claim 2: _enrich_document does not re-read from pg_store at execution time

Verified in `enrichment_tasks.py` lines 232-427. The function signature at line 232 accepts `document_text: str` as a parameter. The body uses `document_text` directly at line 329 (`enrichment.enrich_chunks(document_text=document_text, ...)`), line 311 (`if not document_summary_val and document_text:`), and line 316 (`contextual.generate_document_summary(text=document_text, ...)`).

There is no call to any store to re-fetch the current document content. The content used for LLM enrichment, summary generation, and HyPE question generation is entirely derived from the frozen `document_text` argument.

The only dynamic re-read is a single field at line 390:

```python
extra_payload["visibility"] = await kb_config.get_kb_visibility(org_id, kb_slug, pool)
```

This refreshes the visibility flag from the KB config — not the document content. The Qdrant upsert at lines 412-427 uses the enriched version of the frozen `document_text`, not a fresh read.

### Claim 3: queueing_lock applies to todo state only

Verified against official Procrastinate documentation (https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html):

> "queueing_lock allows a single job in todo status. Meanwhile, it allows multiple jobs to be in doing status."

This is the critical point. If a task is currently executing (state = `doing`) when a second direct-POST arrives, the second `defer_async` call succeeds — no `AlreadyEnqueued` is raised. A new job enters `todo`. This new job contains the latest content (c2). The currently-running job (with c1) finishes and writes enriched c1 vectors. The newly-queued job (c2) then runs and writes enriched c2 vectors. In this scenario, the bug does NOT manifest.

The bug manifests only in the narrower window where the first job is still `todo` (queued but not yet picked up by a worker) when the second POST arrives.

### Claim 4: the bug window is "between Phase 1 completion and worker pickup"

Confirmed. The race window is:

- T=0: POST with c1 → Phase 1 writes raw c1 vectors → enrichment task deferred with c1 frozen (state: `todo`)
- T=δ (before worker picks up the task): POST with c2 → Phase 1 writes raw c2 vectors (overwriting c1) → `defer_async` raises `AlreadyEnqueued` because a `todo` job already exists for the same lock key
- T=worker_pickup: Worker reads the `todo` job (c1 payload), runs `_enrich_document` with `document_text=c1`, writes enriched c1 vectors → **overwrites the raw c2 vectors that Phase 1 had written**

Net effect: the user's latest upload (c2) ends up with enriched-c1 vectors. No error is raised. The `AlreadyEnqueued` exception at line 562 is caught and logged as an INFO event (`enrichment_already_queued`) — silent from the user's perspective.

### One important nuance the claim understates

There is an additional window that was NOT described in the original claim. Because `queueing_lock` only blocks `todo` state, a third scenario exists:

- T=0: POST with c1 → task enters `todo`
- T=1: Worker picks up c1 task (state: `doing`)
- T=2: POST with c2 → Phase 1 writes raw c2 vectors; `defer_async` SUCCEEDS (c1 is `doing`, not `todo`) → new task with c2 enters `todo`
- T=3: c1 task finishes → writes enriched c1 vectors → OVERWRITES raw c2 vectors
- T=4: c2 task runs → writes enriched c2 vectors (correct final state)

In this scenario, there is a temporary window (T=3 to T=4) where Qdrant has stale enriched-c1 vectors for a path whose raw content is c2. For a retrieval call landing in this window, the system would return results based on c1's enrichment context (summary, HyPE questions) applied to c1's chunks — while the user has already uploaded c2. This is a shorter-lived inconsistency that self-heals when the c2 task completes.

## Current behavior

The `ingest_document` route (lines 464-569) performs a two-phase write for direct uploads:

**Phase 1 (synchronous, within the HTTP request):** Chunk the document, embed with raw dense+sparse vectors, upsert to Qdrant via `qdrant_store.upsert_chunks()` (line 511). This gives an immediately-searchable baseline.

**Phase 2 (async, Procrastinate):** Enqueue an enrichment task that will run LLM context generation, HyPE questions, and produce higher-quality embeddings, then overwrite Phase 1 vectors in Qdrant. The task carries a frozen copy of the document content in its JSONB payload.

The `queueing_lock` key is `f"{org_id}:{kb_slug}:{path}"`. This prevents duplicate enrichment tasks from accumulating for the same document path. When a task is already in `todo` state for a given path, subsequent `defer_async` calls raise `AlreadyEnqueued`, which is silently caught (lines 562-568).

The **gitea_webhook** path (lines 707-725) avoids this problem entirely by:

1. Not passing content to the task — only `org_id`, `kb_slug`, `path`, `gitea_repo`, `user_id`
2. Using `schedule_in=timedelta(seconds=settings.ingest_debounce_seconds)` to delay execution
3. Having `ingest_from_gitea` (ingest_tasks.py lines 52-66) fetch the current content from Gitea at execution time

This is the "re-fetch at execution time" pattern. The gitea path is immune to the race condition because all intermediate saves are collapsed: whatever version is in Gitea when the worker runs is what gets ingested.

The direct-POST path cannot use the same pattern because there is no canonical store to re-fetch from — the content lives only in the HTTP request body.

## Industry standard (2026)

### How mature RAG pipelines handle write-write races

The core tension in any async document ingestion pipeline is that content is provided push-style (via API or webhook) but processing is pull-style (worker picks up job from queue). When multiple updates arrive for the same document before a worker can process any of them, the system must decide: process every version (expensive, potentially wrong), process the latest version (correct), or process an arbitrary version (buggy).

Mature RAG pipeline implementations such as LlamaIndex's async ingestion pipeline and Haystack's document store indexers converge on a shared principle: **the content passed to a worker should be the version that is authoritative at execution time, not the version that was authoritative at enqueue time.** LlamaIndex's async pipeline achieves this by storing documents in a central `docstore` and passing only document IDs to workers; the worker fetches from the docstore at execution time, getting whatever version is current. This is functionally identical to the `ingest_from_gitea` pattern in this codebase.

### The debounce-and-re-fetch pattern

The combination of a deduplication key (to collapse rapid updates into a single task slot) with a re-fetch at execution time is the industry-standard approach for document ingestion debouncing. Inngest's documentation on debouncing in queuing systems describes the pattern: "the debounce ensures only one execution occurs after the delay, and the function reads fresh state at execution time." BullMQ (the dominant Node.js task queue) implements exactly this as its recommended deduplication pattern. The gitea_webhook path in this codebase implements the correct version of this pattern.

The variant used in the direct-POST path — freeze content in args, deduplicate via queueing_lock — is what Inngest calls "debounce with stale payload," and is only correct when content is immutable or can only grow (append-only logs, event streams). For mutable documents where users can overwrite entire content, it is incorrect.

### Kafka / event-sourced pipelines

In event-sourced systems using Kafka or Debezium, the canonical solution is partition-key ordering: all events for a given document key land on the same partition and are processed in strict order. This guarantees that a worker processing event N will always see all prior events for that key in order, and that the final write reflects the last event. The equivalent in a PostgreSQL-backed task queue is the combination of `queueing_lock` (deduplication) + content stored as a mutable row in a canonical table + re-fetch at execution time. The direct-POST path currently misses the canonical-table leg.

### Content-hash idempotency as a safety net (but not a fix)

Some pipelines attach a content hash to each job and check at the start of execution whether the Qdrant vectors for that document already reflect a newer hash. This catches the specific window where enriched-old-content overwrites raw-new-content. It does not prevent the window from opening, but it closes it quickly. It requires a source-of-truth for "current content hash" — typically the artifacts table.

## Fix recommendations

Ranked from minimal change to ideal architecture:

### Fix 1 (Minimal change): Replace queueing_lock with cancel-and-re-enqueue

Remove the `AlreadyEnqueued` catch block. Instead, before calling `defer_async`, cancel any existing `todo` task for the same lock key using Procrastinate's job-cancellation API, then enqueue a new task with the current content. This is a 10-15 line change.

Tradeoff: Cancellation is a separate database operation; there is a small window between the cancel and the new enqueue where a worker could pick up the old task. Procrastinate does not provide an atomic "cancel-and-replace" operation. Requires Procrastinate 2.x for `App.cancel_job_by_id`.

### Fix 2 (Recommended): Store content in pg_store, pass artifact_id only

On every direct-POST, write the document text to a canonical table (e.g., `knowledge.artifacts.raw_text` or a new `knowledge.document_versions` table). Pass only the `artifact_id` to the enrichment task. At execution time, the task reads the current content from the table.

This matches the LlamaIndex docstore pattern and the gitea_webhook pattern. It makes the enrichment task immune to race conditions regardless of queueing_lock semantics.

Tradeoff: Requires a schema migration to add a raw_text column (or a new table). The artifacts table already tracks the canonical version of a document; adding `raw_text` to it is a natural fit. Cost: 1 migration + changes to `_enrich_document` to read from pg_store.

### Fix 3 (Defensive safety net, addable now without schema changes): Content-hash guard

At the start of `_enrich_document`, read the artifact's `extra` JSONB from pg_store and check whether it contains a content hash for the current `artifact_id`. Compare against a hash of the `document_text` arg. If the hashes diverge (meaning a newer POST arrived after this task was enqueued), abort and let the newer task handle enrichment.

This requires the direct-POST path to write a content hash into `extra` on every upsert. It is an additive change (no schema migration) and reduces the blast radius of the bug without eliminating it.

Tradeoff: Adds a pg_store read at the start of every enrichment task. The hash write in the POST path must happen atomically with the Phase 1 Qdrant write. Does not prevent the window from opening; only ensures the stale task self-aborts.

### Fix 4 (Structural): Use schedule_in for the direct-POST path too

This is only viable if the direct-POST path can tolerate a delay before enrichment is visible. Schedule the enrichment task `schedule_in=timedelta(seconds=N)`. During that window, additional POSTs collapse via `AlreadyEnqueued`. When the task fires, it re-fetches content from... the problem: there is no Gitea-equivalent for direct uploads.

This fix requires Fix 2 to be viable (store raw content in pg_store so there is something to re-fetch). Once Fix 2 is in place, adding `schedule_in` provides an additional defense layer. Together they match the exact pattern used by gitea_webhook.

## Risk assessment

**Likelihood:** Low-to-medium in practice. The bug window is the period between Phase 1 completion and worker pickup of the enrichment task. On the interactive queue (`enrich-interactive`), worker pickup typically occurs within seconds. The window closes quickly under normal load.

The window stays open longer when:
- Worker capacity is saturated (queue depth > 0)
- The Procrastinate worker is restarting or overloaded
- High upload frequency for the same document (e.g., programmatic re-upload loops)

**Impact when triggered:** Medium. The user sees their latest upload (c2) appear to work (Phase 1 returns 200 OK), but the enriched vectors (context, HyPE questions) reflect c1. Retrieval quality for that document will be degraded until the next upload triggers a successful enrichment. The user has no indication anything went wrong — no error, no log visible to them.

**Affected callers:** The bug affects all callers of the direct-POST ingest endpoint (portal-api upload flow, partner API, any programmatic caller using `POST /ingest/v1/ingest`). It does not affect the gitea_webhook path, crawl tasks, or connector-driven ingestion (those paths use separate task signatures and do not use this queueing_lock pattern for the content-bearing phase).

**Combined assessment:** This is a real correctness bug with low triggering probability and medium user-visible impact. It is not a data-loss bug (no content is permanently lost — the next upload recovers), but it introduces a silent quality degradation window. Fix 2 is the correct long-term fix. Fix 3 (content-hash guard) can be added now as a defensive measure while Fix 2 is being scoped.

## References

- Procrastinate queueing_lock documentation: https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html
- Procrastinate locks (doing-state semantics): https://procrastinate.readthedocs.io/en/stable/howto/advanced/locks.html
- Inngest debouncing in queuing systems: https://www.inngest.com/blog/debouncing-in-queuing-systems-optimizing-efficiency-in-async-workflows
- Postgres-based debounce implementation: https://dev.to/inngest/debounce-messages-in-queueing-systems-how-to-do-it-with-postgres-4jmj
- LlamaIndex async ingestion pipeline (docstore pattern): https://developers.llamaindex.ai/python/examples/ingestion/async_ingestion_pipeline/
- Kafka idempotent consumer pattern: https://nejckorasa.github.io/posts/idempotent-kafka-procesing/
- Debezium JDBC connector upsert semantics: https://debezium.io/documentation/reference/stable/connectors/jdbc.html
