# Runbook: derived_product_failed

Alert: `derived_product_failed` (`deploy/grafana/provisioning/alerting/ingest-rules.yaml`).

## Meaning

Every document version carries one state per derived product (`enrichment`,
`graph`) in `knowledge.artifacts.extra`: `pending`, `done`, `failed` with a
reason, or `not_applicable` with a reason
(`klai-knowledge-ingest/knowledge_ingest/derived_state.py`). The event is
logged once, at error level, when a version's product moves to `failed`:

```
event:derived_product_failed artifact_id=<id> org_id=<org> kb_slug=<kb> product=<enrichment|graph> reason=<reason>
```

The version's raw text stays searchable (`index_status = synced`); what is
missing is the enriched chunks or the graph facts. Nothing re-queues the work
by itself: the document stays failed until its source changes and is synced
again, or someone re-queues it by hand.

A job for a version that was replaced or deleted, or whose connector is going
away, is abandoned without a state, so it never raises this alert. A document
that already says failed for a reason does not alert again for that reason
until something changes: a new version, a new writer build releasing a
quarantine, or a deliberate re-queue.

When the queue itself is unavailable, every document ingested in that window
records `failed (queue_unavailable)` for each product it needed, and the
ingest requests fail. Nothing retries those documents by themselves: once the
queue is back they need a deliberate re-queue (below), or a new sync of their
source.

## Triage

```
service:knowledge-ingest AND event:derived_product_failed | stats by (product, reason) count()
```

Many documents with one reason in a short window is an outage of whatever that
reason names; a single document is about that document.

| reason | product | what happened |
|---|---|---|
| `retries_exhausted` | both | The job failed on every attempt. The preceding error lines with the same `artifact_id` carry the cause. |
| `llm_capacity_exhausted` | enrichment | The LLM had no capacity for the whole deferral window (`llm_capacity.py`). |
| `no_stored_text` | both | The row has no `document_text`; the job had nothing to work from. |
| `quarantined` | graph | FalkorDB died repeatedly while writing this document's episode, so the document is held out of the graph for this writer build. |
| `queue_unavailable` | both | The job could not be queued after the version row was written; the ingest request failed too. |
| `stuck_job_not_covered` | both | Zombie recovery found a stuck job whose queueing lock another live job holds, and that job builds something else. |
| `rebuild_failed` | enrichment | A `rebuild_kb` run deleted the document's parent chunks and its enrichment then failed. |
| `ingest_interrupted` | both | The ingest request died after creating the version and before recording an outcome; the stale-pending reaper found it pending with no job and marked its text failed as well (`stale_pending_artifacts_failed`). Look for the ingest error for that path around the version's creation time. |

## Re-queueing

Re-queueing rewrites production knowledge data, so it follows the approval
rule in `AGENTS.md`: a dry run that counts, the owner's yes for that run, one
knowledge base first.

- Enrichment: `python -m knowledge_ingest.scripts.requeue_failed_artifacts --dry-run`
  selects active versions whose enrichment state is `failed`.
- Graph: a new sync of the source document queues a fresh extraction. For a
  quarantined document, first find out why FalkorDB dies on it.
