# Knowledge Integration

Klai Knowledge provides per-tenant document retrieval. The portal is responsible for provisioning the knowledge infrastructure when a tenant is created. At query time, retrieval is fully automatic via a LiteLLM hook — the portal makes no API call.

## How it works

1. **Provisioning** (tenant creation): portal-api creates the LiteLLM team, scoped API key, and personal knowledge base.
2. **Ingest** (ongoing): the klai-docs personal KB has a Gitea push webhook registered to `knowledge-ingest`. Pushing a document triggers automatic chunking, embedding, and storage in Qdrant.
3. **Retrieval** (query time): `KlaiKnowledgeHook` in LiteLLM intercepts every request, reads `org_id` from the key metadata, retrieves relevant chunks from `knowledge-ingest`, and injects them into the prompt context. No portal involvement.

---

## Provisioning flow

Tenant provisioning runs in `klai-portal/backend/app/services/provisioning.py`.

### Step 2 — LiteLLM team + scoped key

portal-api calls the LiteLLM admin API to create a team and generate a scoped key. The key carries `org_id` (Zitadel org ID) in its metadata:

```json
{
  "metadata": { "org_id": "<zitadel_org_id>" }
}
```

`org_id` is what enables per-tenant retrieval scoping in the LiteLLM hook. The key is stored in `portal_orgs.litellm_team_key` (migration `l2m3n4o5p6q7`) and injected as `LITELLM_API_KEY` into the tenant's LibreChat container.

### Step 5 — Personal knowledge base

portal-api calls klai-docs to create a personal KB:

```
POST http://docs-app:3000/api/orgs/{slug}/kbs
Header: X-Internal-Secret: <DOCS_INTERNAL_SECRET>
```

klai-docs responds by:
1. Creating a Gitea repository for the tenant.
2. Registering a push webhook pointing to `knowledge-ingest` — no further portal action required.

---

## LiteLLM team key scoping

| Property | Value |
|----------|-------|
| Metadata key | `org_id` |
| Value | Zitadel org ID of the tenant |
| Stored in | `portal_orgs.litellm_team_key` |
| Used by | `KlaiKnowledgeHook` inside LiteLLM |
| Set as | `LITELLM_API_KEY` in the tenant's LibreChat container |

**Master key behaviour:** Calls made with the LiteLLM master key have no `org_id` in metadata. The hook detects this and skips retrieval — graceful degradation rather than an error.

---

## Internal ingest/retrieval API

All endpoints are internal Docker network only and are not exposed via Caddy.

| Endpoint | Purpose |
|----------|---------|
| `POST http://knowledge-ingest:8000/ingest/v1/document` | Ingest a document directly |
| `POST http://knowledge-ingest:8000/ingest/v1/webhook/gitea` | Gitea push webhook (auto-registered on KB creation) |
| `POST http://knowledge-ingest:8000/ingest/v1/crawl` | Crawl a URL and ingest the content |
| `POST http://knowledge-ingest:8000/knowledge/v1/retrieve` | Retrieve relevant chunks (called by LiteLLM hook) |

The portal does not call these endpoints directly.

---

## Audio Transcription Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/klai-scribe/v1/transcriptions/{id}/summarize` | Generate AI summary for a transcription |

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
| `LITELLM_API_URL` | LiteLLM admin API base URL (team/key creation) |
| `LITELLM_MASTER_KEY` | Authenticates admin API calls to LiteLLM |
| `DOCS_INTERNAL_SECRET` | `X-Internal-Secret` header for KB creation calls to docs-app |
