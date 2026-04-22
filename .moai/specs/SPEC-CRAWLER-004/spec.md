---
id: SPEC-CRAWLER-004
version: "1.1"
status: completed
created: 2026-04-22
updated: 2026-04-22
author: Mark Vletter
priority: high
issue_number: 108
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-04-22 | Mark Vletter | Initial draft na E2E test op Voys/support KB |
| 1.1 | 2026-04-22 | Mark Vletter | Fase F amendment: `ImageRef` + `DocumentRef.images` retained (used by github + notion adapters). REQ-04.2 scope narrowed to `DocumentRef.content_fingerprint`. Full consolidation tracked under SPEC-KB-IMAGE-002 (#111) and possible SPEC-KB-IMAGE-003. Status: `completed`. |

---

# SPEC-CRAWLER-004: Web-crawl pipeline consolidation in knowledge-ingest

## Context

Tijdens een end-to-end test op 2026-04-22 (Voys tenant, KB `support`, connector
`help.voys.nl` met `max_pages=20`) kwamen vijf bugs en één architecturele fout aan het
licht. De vijf directe bugs zijn inmiddels gefixed in commits
`28dda391` (image-src validatie + `source_type=crawl` + dedup) en `b1abd3e9`
(`chunk_type` schrijven naar Qdrant payload). De architecturele fout blijft: Klai heeft
**twee volledig aparte web-crawl pipelines**, die elkaar overlappen en waarvan de ene
pipeline (klai-connector's `webcrawler.py`) structureel belangrijke features mist.

### De twee pipelines

**Pipeline A** in `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` +
`routes/crawl.py` — volledige SPEC-CRAWL-002/003 en SPEC-CRAWLER-002/003 implementatie:
dual-hash dedup (`knowledge.crawled_pages`), `page_links` + link-graph enrichment
(`anchor_texts`, `incoming_link_count`, `links_to`), Layer A/B/C, AI-selector detection
+ `crawl_domains`, canary/login_indicator auth guard.

**Pipeline B** in `klai-connector/app/adapters/webcrawler.py` — minimal BFS + sitemap
supplement + Layer A + cookies, maar mist dual-hash dedup, `page_links`, link-graph,
login_indicator, AI-selector. Dit is de pipeline die de portal UI "Sync now"-knop
aanroept, dus iedere nieuwe web-klant raakt Pipeline B.

### Drie klassen van kennis-ingress

Analyse van de bestaande adapters wijst op drie karakteristiek verschillende soorten
ingress, niet twee:

- **Klasse 1 — Managed source integrations** (github, notion, google_drive, ms_docs):
  auteur beheert structuur, stable document-IDs, source-native change detection (tree
  SHA, `last_edited_time`, changes API), geen content-quality checks nodig. Past
  Airbyte/LangChain DocumentLoader pattern. **274-415 regels per adapter**.
- **Klasse 2 — Push signals** (scribe transcripts, personal-MCP saves): event-driven,
  direct naar `knowledge-ingest`, zit al buiten klai-connector. **Precedent bestaat.**
- **Klasse 3 — Content acquisition from unmanaged sources** (web crawl): BFS graph
  traversal, fluïde URL-identiteit, dual-hash dedup noodzakelijk, Layer A/B/C, link-
  graph is zelf kennis. Past **niet** in het adapter-pattern. **809 regels adapter**.

Industry-standard (Elastic Enterprise Search, Algolia, Meilisearch, Typesense,
Weaviate) scheidt data integrations (Airbyte/Fivetran) van crawlers (Firecrawl/
Common Crawl). Klai's `klai-connector/BaseAdapter` lekt momenteel crawl-concepts
(`ImageRef`, `DocumentRef.content_fingerprint`, `DocumentRef.images`) die de andere
adapters nooit gebruiken.

### Doel

Eén crawl-pipeline, gelocaliseerd in `knowledge-ingest`. `klai-connector` blijft puur
data-integration adapter-framework voor Klasse 1. Credentials worden gedeeld via een
nieuwe shared library zodat geen plaintext cookies over het netwerk gaan. Resulteert
in: minder code-duplicatie, feature parity voor alle web-klanten, één plek voor nieuwe
crawl-features.

---

## Scope

### In scope

1. Nieuwe shared library `klai-libs/connector-credentials/` (Python package of
   equivalent repo pattern) die `ConnectorCredentialStore` levert aan zowel
   `klai-portal/backend` als `klai-connector` als `klai-knowledge-ingest`.
2. Port image-extractie met URL-validatie (`is_valid_image_src`,
   `dedupe_image_urls`) naar `knowledge-ingest/adapters/crawler.py`.
3. Implementatie van `login_indicator` selector check (Layer B uitbouw) in
   `knowledge-ingest`.
4. Nieuwe endpoint `POST /ingest/v1/crawl/sync` in `knowledge-ingest` die een volledige
   crawl-config accepteert en de bestaande Procrastinate `run_crawl` task orkestreert.
5. Delegation in `klai-connector/app/services/sync_engine.py`: voor
   `connector_type == "web_crawler"` wordt de WebCrawlerAdapter-flow vervangen door één
   HTTP-call naar het nieuwe endpoint.
6. Verwijdering van `klai-connector/app/adapters/webcrawler.py`,
   `app/services/content_fingerprint.py`, `ImageRef`, `DocumentRef.content_fingerprint`,
   `DocumentRef.images`, en de bijbehorende tests.
7. Documentatie-update in `docs/architecture/knowledge-ingest-flow.md`.

### Out of scope (What NOT to Build)

- Geen wijzigingen aan portal UI connector-wizard — achter de schermen verandert alleen
  de sync-orchestratie, de UI-flow blijft identiek.
- Geen wijzigingen aan GitHub/Notion/Drive/MS-Docs adapters — die blijven Klasse 1 in
  klai-connector.
- Geen wijzigingen aan de scribe push-flow — die zit al buiten klai-connector.
- Geen migratie van bestaande `portal_orgs.connector_dek_enc` /
  `portal_connectors.encrypted_credentials` rijen — alleen de code die ze leest
  verhuist naar de shared lib.
- Geen nieuwe Qdrant collectie; `klai_knowledge` blijft.
- Geen wijzigingen aan Procrastinate queue-configuratie (alleen meer werk in bestaande
  `ingest-kb` / `enrich-bulk` queues).
- Pre-existing test-failures in `klai-knowledge-ingest/tests/test_crawl_link_fields.py`
  (`httpx`-attribuut) en `test_knowledge_fields.py` (`fact`/`factual` rename) worden
  meegenomen in Fase G maar niet hier opgelost — losse bugfix.
- Geen nieuwe connector-types; `web_crawler` als enum-waarde blijft bestaan.

---

## Requirements (EARS)

### REQ-CRAWLER-004-01 — Shared connector credentials library

**REQ-01.1 (Ubiquitous).** The system shall provide a shared library
`klai-libs/connector-credentials/` exposing a `ConnectorCredentialStore` class with
`get_or_create_dek()`, `encrypt_credentials()`, and `decrypt_credentials()` methods,
using AES-256-GCM (`AESGCMCipher`) for both KEK and DEK.

**REQ-01.2 (Ubiquitous).** The shared library shall be importable by
`klai-portal/backend`, `klai-connector`, and `klai-knowledge-ingest` via a single
declared dependency, with no version drift tolerated between services.

**REQ-01.3 (Event-driven).** When `klai-knowledge-ingest` needs cookies for a web
crawl, it shall load the connector row by `connector_id`, fetch the org-scoped DEK
via the shared library, and decrypt the cookies in-process. Plaintext cookies shall
never leave the service boundary.

**REQ-01.4 (Unwanted behaviour).** If the `ENCRYPTION_KEY` env var is missing or not
64 hex chars, every service that imports `ConnectorCredentialStore` shall fail
startup with a clear error.

**REQ-01.5 (Ubiquitous).** The shared library shall ship with its own pytest suite
covering encrypt/decrypt round-trip, cross-org isolation, and KEK rotation, runnable
in CI without requiring any service to be started.

### REQ-CRAWLER-004-02 — Crawl pipeline feature parity

**REQ-02.1 (Ubiquitous).** `klai-knowledge-ingest/knowledge_ingest/adapters/
crawler.py` shall extract images from the crawl4ai `media.images` field during
`_ingest_crawl_result`, applying `is_valid_image_src` filtering (rejecting srcset
fragments like `quality=90`, `fit=scale-down`) and `dedupe_image_urls` before
passing them to S3 upload.

**REQ-02.2 (Ubiquitous).** Image upload from the knowledge-ingest crawl adapter
shall use the existing content-addressed Garage/S3 storage pattern (SHA-256 of
bytes as key, tenant-scoped path `{org_id}/images/{kb_slug}/{hash}.{ext}`),
matching the current klai-connector behaviour bit-for-bit.

**REQ-02.3 (Event-driven).** When a crawl-config includes a `login_indicator`
selector, the knowledge-ingest crawl pipeline shall check for its presence on every
extracted page; if the selector matches, the sync shall fail with a single
structured error (`error_type: "auth_wall_detected"`) logged to `sync_runs.error`
and no artifacts shall be upserted.

**REQ-02.4 (Unwanted behaviour).** If image download fails with HTTP 4xx/5xx, the
page ingest shall continue for the remaining valid images and log the failure at
`warning` level with the original URL; it shall never retry-loop on the same URL.

**REQ-02.5 (Ubiquitous).** Qdrant payload fields `source_label`, `source_type`,
`source_domain`, `anchor_texts`, `incoming_link_count`, `links_to`, and
`chunk_type` shall be populated for every chunk produced by the consolidated
pipeline, matching the values currently produced by Pipeline A
(`knowledge-ingest/routes/crawl.py`).

### REQ-CRAWLER-004-03 — Bulk-sync endpoint and delegation

**REQ-03.1 (Ubiquitous).** `knowledge-ingest` shall expose
`POST /ingest/v1/crawl/sync` protected by `InternalSecretMiddleware`
(`X-Internal-Secret` header), accepting a body of `{connector_id, org_id, kb_slug,
base_url, max_pages, path_prefix, content_selector, canary_url,
canary_fingerprint, login_indicator}`.

**REQ-03.2 (Event-driven).** When the endpoint receives a valid request, it shall
load cookies via `ConnectorCredentialStore.decrypt_credentials()` using the
provided `connector_id`, enqueue a Procrastinate `run_crawl` task with the full
resolved config, and return `{job_id, status: "queued"}` within 500 ms.

**REQ-03.3 (Ubiquitous).** `klai-connector/app/services/sync_engine.py` shall,
for `connector_type == "web_crawler"`, bypass the adapter pipeline entirely and
instead POST the connector config (minus credentials — only `connector_id` is
sent) to `POST /ingest/v1/crawl/sync`, mapping the returned `job_id` to
`sync_runs.cursor_state.remote_job_id`.

**REQ-03.4 (State-driven).** While a `web_crawler` sync is in progress,
`klai-connector.sync_engine` shall continue to own `sync_runs` state and events in
the portal schema; it shall poll or subscribe to the remote `job_id` for status
transitions and emit `product_events` exactly once per sync lifecycle.

**REQ-03.5 (Unwanted behaviour).** If `POST /ingest/v1/crawl/sync` is unreachable
or returns a non-2xx, `klai-connector.sync_engine` shall mark the sync_run as
`failed` with `error.details.service = "knowledge-ingest"` and shall not retry
automatically within the same sync cycle.

### REQ-CRAWLER-004-04 — Removal of the duplicate pipeline

**REQ-04.1 (Ubiquitous).** After Fase F, the repository shall contain no file
named `webcrawler.py` or `content_fingerprint.py` under `klai-connector/app/`, and
no class named `WebCrawlerAdapter` anywhere under `klai-connector/`.

**REQ-04.2 (Ubiquitous).** [AMENDED v1.1] `klai-connector/app/adapters/base.py` shall not
contain `DocumentRef.content_fingerprint` after Fase F. `ImageRef` + `DocumentRef.images`
are RETAINED — github and notion adapters actively populate them to drive sync_engine's
`_upload_images` path. Full removal + consolidation of the klai-connector image stack is
tracked under SPEC-KB-IMAGE-002 (#111 — shared `klai-libs/image-storage/` package) and
possible SPEC-KB-IMAGE-003 (adapter contract rework to surface images as inline markdown).

**REQ-04.3 (Ubiquitous).** `klai-connector/app/adapters/registry.py` shall route
`web_crawler` connector_type directly to the delegation path added in REQ-03.3,
not via a `BaseAdapter` implementation.

**REQ-04.4 (Ubiquitous).** All pytest files that test the removed webcrawler
adapter (e.g. `tests/adapters/test_webcrawler.py`,
`tests/adapters/test_webcrawler_canary.py`) shall be deleted. Tests that exercise
shared helpers (image_utils, sync_engine) shall be updated to reflect the new
smaller surface area.

**REQ-04.5 (Unwanted behaviour).** If during Fase F any import of a removed symbol
is left anywhere in the repo, CI shall fail the fase with a collection error, not
a runtime error — `ruff` / `pyright` must catch the dangling reference before
deploy.

### REQ-CRAWLER-004-05 — Validation, smoketest, and documentation

**REQ-05.1 (Event-driven).** When Fase E runs, the Voys tenant `support` KB shall
be re-synced via the new `/ingest/v1/crawl/sync` endpoint; after completion, the
Qdrant payload for every chunk shall contain `source_type="crawl"`,
`source_label="help.voys.nl"`, non-empty `anchor_texts`, `chunk_type` drawn from
`{procedural, conceptual, reference, warning, example}`, and for hub pages
(`index.md`) `incoming_link_count > 0`.

**REQ-05.2 (Event-driven).** When Fase E runs, `knowledge.crawled_pages` shall
contain exactly one row per distinct source URL (20 for the Voys `support`
smoketest); `knowledge.page_links` shall contain every internal link extracted
from the crawled HTML; a subsequent no-op re-sync shall emit
`crawl_skipped_unchanged` for every URL via structlog.

**REQ-05.3 (Event-driven).** When Fase E runs against the Redcactus connector
(`wiki.redcactus.cloud`) with expired cookies, the sync shall fail loudly
(`sync_runs.status == "failed"`, `error_type == "auth_wall_detected"`, no
artifacts upserted). With valid cookies, the sync shall complete and chunks shall
have `source_label="wiki.redcactus.cloud"`.

**REQ-05.4 (Ubiquitous).** A regression check shall grep the logs of
`klai-connector` and `knowledge-ingest` during a full sync; plaintext cookie
values (longer than 30 chars) shall not appear in any log line, any sync_run
error, or any `product_events` payload.

**REQ-05.5 (Ubiquitous).** `docs/architecture/knowledge-ingest-flow.md` § Part 1.2
("External sources via klai-connector"), § Part 2 ("Phase 1 Step 1 — Parse and
chunk"), and § Part 4 ("Tenant provisioning") shall be updated to reflect the
consolidated pipeline before Fase G closes.

---

## Affected Files

### klai-libs (new)

- `klai-libs/connector-credentials/pyproject.toml` (new)
- `klai-libs/connector-credentials/connector_credentials/__init__.py` (new)
- `klai-libs/connector-credentials/connector_credentials/store.py` (new, contains
  `ConnectorCredentialStore`)
- `klai-libs/connector-credentials/connector_credentials/cipher.py` (new, moved from
  `klai-portal/backend/app/core/security.py`)
- `klai-libs/connector-credentials/tests/test_store.py` (new)

### klai-portal/backend (refactor)

- `klai-portal/backend/app/services/connector_credentials.py` → thin re-export from
  shared lib
- `klai-portal/backend/pyproject.toml` → add shared lib dep

### klai-knowledge-ingest (additions)

- `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` → add image
  extraction, `login_indicator` check
- `klai-knowledge-ingest/knowledge_ingest/image_utils.py` (new) → `is_valid_image_src`,
  `dedupe_image_urls` (ported from klai-connector)
- `klai-knowledge-ingest/knowledge_ingest/s3_storage.py` (new, ported from klai-
  connector with minimal changes)
- `klai-knowledge-ingest/knowledge_ingest/routes/crawl.py` → add
  `POST /ingest/v1/crawl/sync` endpoint
- `klai-knowledge-ingest/knowledge_ingest/crawl_tasks.py` → accept new config
  fields (cookies via credential lookup, login_indicator)
- `klai-knowledge-ingest/pyproject.toml` → add shared lib dep, `filetype`
- `klai-knowledge-ingest/tests/test_crawler_images.py` (new)
- `klai-knowledge-ingest/tests/test_crawler_login_indicator.py` (new)
- `klai-knowledge-ingest/tests/test_crawl_sync_endpoint.py` (new)

### klai-connector (delegation + removal)

- `klai-connector/app/services/sync_engine.py` → route `web_crawler` to HTTP
  delegation
- `klai-connector/app/clients/knowledge_ingest.py` → add `crawl_sync()` method
- `klai-connector/app/adapters/webcrawler.py` **(delete in Fase F)**
- `klai-connector/app/services/content_fingerprint.py` **(delete in Fase F)**
- `klai-connector/app/services/image_utils.py` **(delete `is_valid_image_src` +
  `dedupe_image_urls` in Fase F — keep `extract_markdown_image_urls` +
  `resolve_relative_url` for GitHub/Drive markdown flow)**
- `klai-connector/app/adapters/base.py` → remove `ImageRef`,
  `DocumentRef.content_fingerprint`, `DocumentRef.images`
- `klai-connector/app/adapters/registry.py` → route `web_crawler` to delegation
- `klai-connector/tests/adapters/test_webcrawler.py` **(delete in Fase F)**
- `klai-connector/tests/adapters/test_webcrawler_canary.py` **(delete in Fase F)**
- `klai-connector/pyproject.toml` → add shared lib dep

### docs (last fase)

- `docs/architecture/knowledge-ingest-flow.md` → § 1.2, § 2, § 4 updated

---

## Delta Markers (brownfield)

### [DELTA] klai-portal/backend (Fase 0)

- [EXISTING] `app/services/connector_credentials.py` — behaviour preserved via thin
  re-export
- [MODIFY] `app/core/security.py` — `AESGCMCipher` moves to shared lib; local import
  becomes re-export
- [NEW] import path `from klai_connector_credentials import ConnectorCredentialStore`

### [DELTA] klai-knowledge-ingest (Fase A–C)

- [EXISTING] `routes/crawl.py` `crawl_url` endpoint, `adapters/crawler.py`
  `run_crawl_job` function
- [MODIFY] `adapters/crawler.py` — `_ingest_crawl_result` adds image extraction +
  Layer B selector; `run_crawl_job` accepts cookies from credential store
- [NEW] `routes/crawl.py` `POST /ingest/v1/crawl/sync` endpoint;
  `image_utils.py` module; `s3_storage.py` module
- [NEW] Qdrant payload field `chunk_type` already registered by commit `b1abd3e9`

### [DELTA] klai-connector (Fase D → F)

- [EXISTING] `services/sync_engine.py` dispatch loop over registered adapters
- [MODIFY] `sync_engine.py` — pre-dispatch fork for `web_crawler` connector_type;
  `clients/knowledge_ingest.py` gains `crawl_sync()` method
- [REMOVE] `adapters/webcrawler.py`, `services/content_fingerprint.py`,
  `ImageRef`, `DocumentRef.content_fingerprint`, `DocumentRef.images`
- [REMOVE] tests `tests/adapters/test_webcrawler.py`,
  `tests/adapters/test_webcrawler_canary.py`
- [MODIFY] `adapters/registry.py` dispatch map

---

## Acceptance Summary

Full Given/When/Then scenarios in `acceptance.md`. Key gates:

1. Shared credentials library round-trip + cross-org isolation (Fase 0)
2. Pipeline A with image extraction produces same 167 Qdrant chunks as current
   Pipeline B, now with `source_label="help.voys.nl"` + `anchor_texts` + correct
   `incoming_link_count` (Fase A–B)
3. New `/ingest/v1/crawl/sync` endpoint queues Procrastinate task and returns 202
   within 500 ms (Fase C)
4. Delegation path: klai-connector fires one HTTP call and tracks remote job_id
   (Fase D)
5. Voys `support` smoketest: all 5 acceptance checks in REQ-05.1 pass (Fase E)
6. Redcactus smoketest: expired-cookie case fails loudly per REQ-05.3 (Fase E)
7. `grep -r webcrawler klai-connector/` returns zero results (Fase F)
8. Full regression suite for GitHub/Notion/Drive connectors green, no imports of
   removed symbols anywhere (Fase F close)
9. Documentation updated (Fase G)

---

## References

- SPEC-CRAWL-002 (Cookie auth), SPEC-CRAWL-003 (Content quality layers A/B/C),
  SPEC-CRAWL-004 (Auth guard, canary)
- SPEC-CRAWLER-002 (Crawl registry), SPEC-CRAWLER-003 (Link-graph retrieval)
- SPEC-KB-IMAGE-001 (Adapter-owned image URL resolution)
- SPEC-KB-020 (Connector credential encryption)
- SPEC-KB-021 (Source-aware enrichment — `chunk_type` landed in commit `b1abd3e9`)
- Recent commits: `28dda391` (image src validation + `source_type=crawl` fixes),
  `b1abd3e9` (`chunk_type` in Qdrant payload)
- `docs/architecture/knowledge-ingest-flow.md` § Part 1.2, Part 2 Phase A–E
