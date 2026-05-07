# klai-retrieval-telemetry

Shared retrieval telemetry helpers for Klai services. Extracted from
`deploy/litellm/klai_knowledge.py` per SPEC-MCP-RETRIEVAL-001 so both the
LiteLLM pre-call hook (LibreChat) and `klai-knowledge-mcp` (third-party
LLMs via OAuth) can emit `retrieval_log` and `gap_events` against the
same portal-api endpoints with the same payload contract.

## What this lib does

Three fire-and-forget telemetry helpers that POST to portal-api after a
successful retrieval:

- `classify_gap(chunks)` — pure function. Returns `"hard"` (no chunks),
  `"soft"` (low scores), or `None` (success).
- `fire_retrieval_log(...)` — POST to `/internal/v1/retrieval-log`.
  Records the chunk_ids and reranker_scores returned for an executed query.
- `fire_gap_event(...)` — POST to `/internal/v1/gap-events`. Records a
  classified gap and the top chunk metadata for triage in admin.

All helpers are fire-and-forget: they schedule the POST via
`asyncio.create_task` and silently swallow HTTP and network errors. A
failed telemetry emit must never break the retrieval pipeline.

## Caller-attribution: `caller_client_id`

The optional `caller_client_id` keyword on the `fire_*` helpers labels
the telemetry row with the OAuth client that made the request:

- `caller_client_id=None` — LibreChat path (the default).
- `caller_client_id="<client_id>"` — third-party MCP client (Claude
  Desktop, Cursor, ChatGPT). The DB-side `client_id` from
  `portal_oauth_clients`, set by SPEC-MCP-AUTH-001 token verification.

Dashboards can split-by `caller_client_id` to compare external traffic
against LibreChat without mixing the two streams.

## Configuration

The helpers read these environment variables on import:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PORTAL_API_URL` | yes | — | Base URL for `/internal/v1/{retrieval-log,gap-events}` |
| `PORTAL_INTERNAL_SECRET` | yes | — | Bearer used to authenticate inbound at portal-api |
| `KLAI_GAP_SOFT_THRESHOLD` | no | `0.4` | Reranker-score below = soft gap |
| `KLAI_GAP_DENSE_THRESHOLD` | no | `0.35` | Dense-score below = soft gap (when no reranker scores) |
| `EMBEDDING_MODEL_VERSION` | no | `bge-m3-v1` | Recorded on each retrieval-log row |

Override via `RetrievalTelemetryConfig` for callers that want to inject
their own values (recommended in tests).

## Why a separate package

Both consumers (LiteLLM hook + knowledge-mcp) write to the **same**
`retrieval_log` table and `gap_events` stream. A duplicated copy in
each consumer would diverge silently when the schema or the gap-classifier
threshold changes. Sharing one package makes the contract explicit.

The retrieval-call helper itself (JWT-or-legacy auth + POST to
`/retrieve`) is **not** in this package — it has different config
requirements per consumer (LiteLLM has its own Zitadel client, MCP has
another) and is small enough to live inline in each.

## Reference

- SPEC-MCP-RETRIEVAL-001 (`.moai/specs/SPEC-MCP-RETRIEVAL-001/`)
- SPEC-KB-015 (retrieval log)
- SPEC-KB-014 (gap detection)
