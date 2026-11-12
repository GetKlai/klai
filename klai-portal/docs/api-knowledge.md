# Knowledge Integration

Klai Knowledge provides per-tenant document retrieval. The portal is responsible for provisioning the knowledge infrastructure when a tenant is created. At query time, retrieval runs inside the portal's own chat path (the LiteLLM hook that used to do it was removed on 21 October 2026).

## How it works

1. **Provisioning** (tenant creation): portal-api creates the LiteLLM team, scoped API key, the personal docs repository and the default knowledge bases.
2. **Ingest** (ongoing): the klai-docs personal KB has a Gitea push webhook registered to `knowledge-ingest`. Pushing a document triggers automatic chunking, embedding, and storage in Qdrant.
3. **Retrieval** (query time): the portal chat path resolves the caller's `org_id`, retrieves relevant chunks through `klai-retrieval-api`, and puts them in the prompt context.

---

## Provisioning flow

Tenant provisioning runs in `klai-portal/backend/app/services/provisioning/orchestrator.py` (`_provision`), a state machine in which every step registers a compensating action. The step numbers below follow that file.

### Step 2 — LiteLLM team + scoped key

portal-api calls the LiteLLM admin API to create a team and generate a key for it (`orchestrator.py:424-458`). The key carries `org_id` (Zitadel org ID) in its metadata and is limited to the models `klai-primary`, `klai-fast` and `klai-large`:

```json
{
  "metadata": { "org_id": "<zitadel_org_id>" }
}
```

`org_id` in the key metadata lets LiteLLM attribute model calls to the tenant. When provisioning completes, the key is stored encrypted in `portal_orgs.litellm_team_key` (`orchestrator.py:653`) and written as `LITELLM_API_KEY` into the tenant's LibreChat environment (`klai-portal/backend/app/services/provisioning/generators.py:329-330`).

### Step 5 — Personal knowledge base

portal-api calls klai-docs to create the tenant's personal docs repository (`klai-portal/backend/app/services/docs_client.py:36-60`):

```
POST http://docs-app:3010/docs/api/orgs/{slug}/kbs
Header: X-Internal-Secret: <DOCS_INTERNAL_SECRET>
Header: X-User-ID: system
```

klai-docs responds by:
1. Creating a Gitea repository for the tenant.
2. Registering a push webhook through `knowledge-ingest`, which owns the webhook secret (`klai-docs/app/api/orgs/[org]/kbs/route.ts:85-91`). No further portal action is required.

Step 6 then creates the portal's own default knowledge bases, the organisation KB `org` and the first user's `personal-<user>` KB (`klai-portal/backend/app/services/default_knowledge_bases.py:1-10`).

---

## LiteLLM team key scoping

| Property | Value |
|----------|-------|
| Metadata key | `org_id` |
| Value | Zitadel org ID of the tenant |
| Stored in | `portal_orgs.litellm_team_key`, encrypted |
| Used by | LiteLLM, to attribute model calls to the tenant |
| Set as | `LITELLM_API_KEY` in the tenant's LibreChat container |

**Master key behaviour:** Calls made with the LiteLLM master key have no `org_id` in metadata, so the PII hooks cannot attribute them to a tenant.

---

## Internal ingest/retrieval API

All endpoints are internal Docker network only and are not exposed via Caddy. Callers authenticate with `X-Internal-Secret`; portal-api uses a separate secret for each target ([api-auth.md](api-auth.md)).

| Endpoint | Purpose |
|----------|---------|
| `POST http://knowledge-ingest:8000/ingest/v1/document` | Ingest a document directly |
| `POST http://knowledge-ingest:8000/ingest/v1/webhook/gitea` | Gitea push webhook (auto-registered on KB creation) |
| `POST http://knowledge-ingest:8000/ingest/v1/crawl` | Crawl a URL and ingest the content |
| `POST http://retrieval-api:8000/retrieve` | Retrieve relevant chunks; called by the portal chat path (`klai-portal/backend/app/services/retrieval_context.py:892-900`) |

portal-api calls knowledge-ingest through `klai-portal/backend/app/services/knowledge_ingest_client.py` and retrieval-api through `KNOWLEDGE_RETRIEVE_URL`.

---

## Audio Transcription Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scribe/v1/transcriptions/{id}/summarize` | Generate AI summary for a transcription |

The portal reaches scribe-api through its BFF proxy at `/api/scribe/*`, which forwards to scribe-api's `/v1/transcriptions/{txn_id}/summarize` (`klai-scribe/scribe-api/app/api/transcribe.py:403`).

**Query parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `force` | bool | Regenerate even if a summary already exists |

**Request body**

| Field | Description |
|-------|-------------|
| `recording_type` | `meeting` or `recording` — determines which prompt set is used |
| `language` | Target language for the generated summary |

**Response**

The response includes a `summary_json` field with the following structure:

| Field | Description |
|-------|-------------|
| `type` | Recording type used for summarization |
| `markdown` | Human-readable summary in Markdown |
| `structured` | Type-specific structured data |

Structured data shape by type:

- `meeting` — topics, decisions, action items
- `recording` — key points, quotes, conclusions

**Summarization process**

Two-phase summarization via LiteLLM:

1. Extract facts (temperature 0.1)
2. Synthesize summary (temperature 0.3)

Storage: result is written to `scribe.transcriptions.summary_json` (JSONB column).

---

## Environment variables

portal-api requires the following variables to provision knowledge infrastructure:

| Variable | Used for |
|----------|---------|
| `LITELLM_MASTER_KEY` | Authenticates admin API calls to LiteLLM (team/key creation) |
| `DOCS_INTERNAL_SECRET` | `X-Internal-Secret` header for KB creation calls to docs-app |
