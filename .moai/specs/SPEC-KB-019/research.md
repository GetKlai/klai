# Research: SPEC-KB-019 Notion Connector

## Architecture Analysis

### Connector Adapter Pattern

The klai-connector uses an abstract base adapter pattern in klai-connector/app/adapters/base.py.

**BaseAdapter Interface Contract:**
- list_documents(connector, cursor_context) -> list[DocumentRef]
- fetch_document(ref, connector) -> bytes
- get_cursor_state(connector) -> dict[str, Any]
- post_sync(connector) -> None (optional)

Each DocumentRef contains: path, ref, size, content_type, source_ref

### GitHub Adapter (Reference Implementation)

Located: klai-connector/app/adapters/github.py

**Authentication:**
- GitHub App JWT (10-min) -> Installation Access Token (1-hour)
- Token caching with 60-second expiry buffer
- Private key from env var, base64-encoded

**Config (JSONB):**
- installation_id: int
- repo_owner: str
- repo_name: str
- branch: str (default: main)
- path_filter: str | None

**DocumentRef Construction:**
- path: relative file path
- ref: Git blob SHA
- source_ref: "{owner}/{repo}:{branch}:{path}"
- content_type: mapped by extension (.pdf -> pdf_document, others -> kb_article)

**Cursor State:**
- {"tree_sha": str}

**Incremental Sync:**
- Compares tree SHA with previous run
- If identical, skips entire sync

**File Discovery:**
- Git Trees API with recursive=1
- Filters by: .md, .txt, .pdf, .docx, .rst, .html, .csv
- Respects optional path_filter glob

**HTTP Client:**
- Single persistent httpx.AsyncClient reused across calls
- Closed gracefully in app shutdown

### WebCrawler Adapter Patterns

Located: klai-connector/app/adapters/webcrawler.py

**Async Job Pattern:**
- CrawlJobPendingError when job still running after 30 minutes
- SyncEngine catches this, marks sync_run as PENDING
- Next sync resumes polling same task_id

**Cursor State for Resume:**
- pending_task_id: str
- job_started_at: str
- base_url: str

**Cache Pattern:**
- Per-connector in-memory cache {connector_id: {url: markdown}}
- Populated in list_documents()
- Fetched in fetch_document()
- Freed in post_sync()

### Sync Engine Integration

Located: klai-connector/app/services/sync_engine.py

**Execution Model:**
- Global semaphore: max 3 concurrent syncs
- Per-connector lock: 1 sync at a time per connector
- Portal is single source of truth for config

**Cursor State Lifecycle:**
1. get_cursor_state() called at start
2. Last pending cursor_state retrieved as cursor_context (if exists)
3. Last successful cursor_state retrieved if no pending
4. Adapter returns new cursor_state
5. Tree SHA optimization (GitHub only)
6. Final cursor_state stored in sync_run

**Per-Document Checkpointing:**
- Every 10 documents: cursor_state updated with ingested_refs
- On crash: resume skips already-ingested docs

### Secret Storage Analysis

**Current State:**
- Connector config stored in portal_connectors.config (JSONB, unencrypted)
- No encrypted_config column exists
- GitHub private key in ENCRYPTION_KEY env var

**For Notion:**
- Token would be in config JSONB (unencrypted risk)
- Recommendation: add encrypted_config column before production
- Alternative: encryption layer in adapter

## Portal Backend Analysis

### ConnectorType and Defaults

File: klai-portal/backend/app/api/connectors.py, line 29

Literal["github", "notion", "web_crawler", "google_drive", "ms_docs"]

**CONTENT_TYPE_DEFAULTS (line 32-38):**
- "notion": "kb_article" (already defined)

### PortalConnector Model

File: klai-portal/backend/app/models/connectors.py

**Relevant Fields:**
- config: JSONB
- allowed_assertion_modes: list | None
- created_by: str
- No encrypted_config column

## Frontend UI Analysis

### Current Forms

File: klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx

**GitHub (2-step):**
1. Type selection
2. Configure: name, installation_id (number), repo_owner, repo_name, branch, path_filter

**WebCrawler (4-step wizard):**
1. Type selection
2. Details: name, base_url, path_prefix
3. Preview: test-crawl sample URL
4. Settings: max_pages, assertion modes

**Config Assembly:**
```typescript
const config: Record<string, unknown> = {}
if (selectedType === 'github') {
  config.installation_id = Number(githubConfig.installation_id)
  config.repo_owner = githubConfig.repo_owner
}
```

### i18n Pattern

**Message Keys:**
- Prefix: admin_connectors_
- Type-specific: admin_connectors_github_*, admin_connectors_webcrawler_*
- Notion keys needed: admin_connectors_notion_token, admin_connectors_notion_database_id

## Dependency Analysis

### Current Unstructured Usage

File: klai-connector/app/services/parser.py

- Text formats: decoded directly as UTF-8
- Binary formats: passed to unstructured.partition.auto.partition()
- Version: 0.16.0+ in pyproject.toml

### Notion Integration

**Recommended: unstructured-ingest[notion]**
- Official Notion connector
- Handles pagination, block conversion, markdown
- API: NotionConnectorConfig + NotionConnector

**Authentication: Internal Integration Token (MVP)**
- Simpler than OAuth
- Stored in config JSONB (needs encryption)

**Incremental Sync: last_edited_time**
- Track max(page.last_edited_time)
- Cursor state: {"last_edited_time": "2026-04-04T...Z", "pages_synced": 42}
- No async polling needed (direct API)

## Config Schema

**Notion Config (JSONB):**
```json
{
  "notion_token": "secret_...",
  "database_id": "uuid",
  "max_pages": 1000
}
```

**Cursor State:**
```json
{
  "last_edited_time": "2026-04-04T12:34:56Z",
  "pages_synced": 42
}
```

## Implementation Pattern

**Adapter:**
1. Extract token and database_id from config
2. Create NotionConnectorConfig(api_key=token)
3. Instantiate NotionConnector
4. Call get_pages() -> DocumentRef list
5. Cache markdown per-connector
6. fetch_document returns cached markdown

**Frontend (3-step):**
1. Type selection
2. Token + Database ID
3. Assertion modes + Max pages

**Startup (main.py):**
```python
registry.register("notion", NotionAdapter(settings))
```

## Reference Implementations

Key file patterns:
- BaseAdapter: klai-connector/app/adapters/base.py:28-63
- Token caching: klai-connector/app/adapters/github.py:67-111
- Config dict: klai-connector/app/adapters/github.py:125-130
- DocumentRef: klai-connector/app/adapters/github.py:149-157
- Cache: klai-connector/app/adapters/webcrawler.py:329-377
- Cursor lifecycle: klai-connector/app/services/sync_engine.py:145-172
- Content defaults: klai-portal/backend/app/api/connectors.py:32-38
- Frontend form: klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx:99-120

## Risks and Constraints

### Token Security (CRITICAL)
- JSONB storage is unencrypted
- Mitigation: Add encrypted_config before production
- Risk: HIGH for production, acceptable for MVP

### unstructured-ingest
- Pre-1.0; API may change
- Pin version after testing

### Notion API Rate Limiting
- 3 req/sec per token
- Large databases may timeout
- Mitigation: Cap at 1000 pages

### Page Block Rendering
- Risk: Custom properties may not parse
- Mitigation: Test with real user databases

---

Generated: 2026-04-04
