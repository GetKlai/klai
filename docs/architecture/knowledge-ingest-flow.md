# Knowledge ingest: how content gets into the knowledge base

Engineering reference for the ingest side of the knowledge system, verified on
12 November 2026 against commit `3987ee2a9` (`origin/main`), with the two later changes
of 12 November (#2340, #2341) folded in and the anchors rechecked at `631b5a82f`.

It covers everything between a source and the stores: where content comes from,
what `klai-knowledge-ingest` does with it, what it writes, how an update and a
delete behave, and which operations exist to redo work. It leaves the rest to
the documents that own it:

- query time (retrieval, reranking, graph search, scopes, visibility, the
  feedback loop): [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md)
- the chat turn that consumes the results: [chat-system.md](chat-system.md)
- the knowledge model and the stores field by field:
  [klai-knowledge-architecture.md](klai-knowledge-architecture.md)
- history, decisions and measurements of the knowledge base:
  [knowledge-base-ingestion-and-retrieval-plan.md](knowledge-base-ingestion-and-retrieval-plan.md)
  (§6, the knowledge-base log)

Anchors are `path:line`. A path that does not start with a top-level directory
of the repository (`klai-connector/`, `klai-portal/`, `deploy/`, ...) is relative
to `klai-knowledge-ingest/knowledge_ingest/`. A statement marked `GAP-*`
refers to a row in [product-gaps-backlog.md](product-gaps-backlog.md).

## The big picture

```
 source ──▶ entry route ──▶ POST /ingest/v1/document ──▶ synchronous part ──▶ Procrastinate jobs
 (Part 1)                     (routes/ingest.py:1083)      (Part 2.1)          (Part 2.3 to 2.5)
                                                              │                      │
                                                              ▼                      ▼
                                                   Postgres artifact row      enrichment: context sentence,
                                                   Qdrant raw points          questions, vectors rewritten
                                                   (searchable now)           graph extraction (FalkorDB)
```

`knowledge-ingest` is one container. It runs the FastAPI app and all
Procrastinate workers in the same Python process (`app.py:34-56`,
`worker.py:149-231`); there is no separate worker container. A document is
searchable as soon as the synchronous part has written its raw points (dense
and sparse vectors, no model call). The asynchronous part replaces those points
with enriched ones and adds the graph extraction.

Every route except `/health` and the Gitea webhook needs the internal secret
header (`middleware/auth.py:22-37`); the webhook checks an HMAC signature
instead (1.2, `routes/ingest.py:1138-1152`). Org and user identity are asserted per route; a
personal knowledge base (`personal-<user>`) needs a matching `user_id`
(`routes/ingest.py:1083-1131`). All tenant tables in the `knowledge` schema are
row-level secured (`klai-knowledge-ingest/alembic/versions/0002_rls_knowledge_schema.py:36-58`), and
workers reach them through `tenant_scoped_connection` (`db.py`). `/health`
checks Qdrant, the embedding service, the sparse service, FalkorDB (when the
graph is on) and that no worker lane is dead; it answers 503 when any of them
is not ok (`app.py:84-140`).

## Part 1: Where content comes from

### 1.1 Every source and its entry route

| Source | Entry into ingest | What is sent |
|---|---|---|
| Docs editor (Gitea-backed pages) | Gitea push webhook `POST /ingest/v1/webhook/gitea` | the task fetches the current file from Gitea; `source_type="docs"`, `content_type="kb_article"` |
| Docs bulk sync | `POST /ingest/v1/kb/sync`, called by klai-docs when a KB is created or recovered | one `ingest_document` call per `.md` in the repository, synchronous, no queue |
| Connectors: github, notion, airtable, confluence, google_drive (aliases google_docs, google_sheets, google_slides), ms_docs, json_feed | klai-connector parses and posts `POST /ingest/v1/document` | `source_type="connector"`, `source_connector_id`, `source_ref` |
| Web crawl (connector type `web_crawler`) | `POST /ingest/v1/crawl/sync`, called by klai-connector | only the connector id and crawl settings; the crawl runs inside ingest (1.4) |
| Crawl wizard | `POST /ingest/v1/crawl/preview`, `POST /ingest/v1/crawl/auth-probe`, `POST /ingest/v1/crawl` (one URL) | preview and probe ingest nothing; `/crawl` fetches and ingests a single URL |
| File upload (portal) | `POST /ingest/v1/document` from portal-api | text files as they are; converted files as pre-made chunks (1.5) |
| MCP writes | `klai-knowledge-mcp` tools `save_personal_knowledge` and `save_org_knowledge` | `source_type="mcp"`, `content_type="kb_article"`, frontmatter with assertion mode and tags |
| Scribe transcripts | `POST /v1/transcriptions/{id}/ingest` in scribe-api, then `POST /ingest/v1/document` | path `scribe/<id>`, `source_type="connector"`, pre-made chunks (1.6) |
| Meeting-bot transcripts | `ingest_vexa_meeting` in portal-api | `POST /ingest/v1/document` |
| Partner API writes | portal-api proxy `partner_knowledge.py` | `POST /ingest/v1/document` |

Anchors: `routes/ingest.py:1132-1262` (webhook), `routes/ingest.py:1595-1622`
(bulk sync), adapter registration `klai-connector/app/main.py:106-135`,
connector payload `klai-connector/app/clients/knowledge_ingest.py:24-80,105`,
crawl routes `routes/crawl.py:125,191,431,512,761,918`,
`klai-knowledge-mcp/main.py:534-580,891,960`,
`klai-scribe/scribe-api/app/api/transcribe.py:477-500`,
`klai-portal/backend/app/services/knowledge_adapter.py:1-30`,
`klai-portal/backend/app/services/partner_knowledge.py:28-45`.
Every caller of `/ingest/v1/document` found in the repository is in this table
(connector, portal-api, knowledge-mcp, scribe-api). Support cases of the
connector type `hubspot_support` are read by the connector and posted to a
portal endpoint; they never reach ingest (`klai-connector/app/services/sync_engine.py:236-247`).

The docs MCP tools (`list_docs_kbs`, `list_docs_pages`, `get_docs_page`,
`create_docs_page`, `update_docs_page`) write to klai-docs, so their content
arrives through the Gitea path of 1.2.

### 1.2 The docs editor

The editor autosaves 1500 ms after the last keystroke
(`klai-portal/frontend/src/routes/app/docs/$kbSlug/$pageId.lazy.tsx:273`). The
save goes through klai-docs to Gitea (one repository per KB, `org-{slug}/{kb}`);
a stale file SHA is answered with 409 and the fresh SHA, and the editor retries
(`klai-docs/app/api/orgs/[org]/kbs/[kb]/pages/[...path]/route.ts:151-202`).

Gitea then calls the webhook. It checks the HMAC signature, accepts only the
main branch, and looks at added and modified `.md` paths that do not start with
`_` (`routes/ingest.py:1132-1262`). For each path it defers the task
`ingest_from_gitea` on queue `ingest-kb` with
`queueing_lock=gitea:{org}:{kb}:{path}` and
`schedule_in=ingest_debounce_seconds` (180 s, `config.py:349`). A save while
the task is still waiting raises `AlreadyEnqueued` and is dropped, so at most
one task per document is pending. The task fetches the file from Gitea when it
runs, never at queue time, and calls `ingest_document`
(`ingest_tasks.py:29-81`). With `enrichment_enabled` off (a global switch,
`config.py:346`) the webhook ingests immediately without the queue.

Removed `.md` paths are purged at once, without debounce
(`routes/ingest.py:1269-1291`). The organisation id is read from the
description of the Gitea organisation (`routes/ingest.py:1293-1312`). A push to
the legacy repository named `personal` carries `users/<id>/` paths, and the
handler rewrites the slug to `personal-<id>` from the path (`routes/ingest.py:1204-1216`).

### 1.3 Connectors: schedule, start, completion

klai-connector is the adapter framework for managed sources. Per connector it
lists documents, parses files, optionally uploads images to object storage, and
posts each document to ingest.

- **Parsing.** Binary formats go through Unstructured `partition.auto`, up to
  50 MB (`klai-connector/app/services/parser.py:14,40-44,69`); text formats are
  decoded directly.
- **Schedule.** The portal holds `portal_connectors.schedule`, a five-field
  crontab. The connector's APScheduler reads
  `GET /internal/scheduled-connectors` at start and every 5 minutes
  (`klai-connector/app/services/scheduler.py:31,63,77-82`) and builds a
  `CronTrigger.from_crontab` per connector (`:97`). Only connectors of
  org-scoped KBs are returned (`klai-portal/backend/app/api/internal.py:642-690`).
  The scheduler is created without a timezone (`klai-connector/app/services/scheduler.py:42`) and the
  compose service sets no `TZ` (`deploy/docker-compose.yml:2303-2373`; the Dockerfile sets none either), so APScheduler 3.11.3 falls back to the
  container's local zone (`apscheduler/schedulers/base.py:905`, `CronTrigger.from_crontab` takes the scheduler's zone). The base image
  `python:3.12-slim` has `/etc/localtime` pointing at `Etc/UTC` (checked in a local copy of the image on 12 November 2026), so the cron
  expressions of connectors are read as UTC.
  The operator view is in [connector-schedules.md](../runbooks/connector-schedules.md).
- **Start.** A scheduled firing and the portal's on-demand call
  (`POST /connectors/{id}/sync`, `klai-connector/app/routes/sync.py:70`, 202)
  both create a `sync_runs` row in status RUNNING and run `SyncEngine.run_sync`.
  Both refuse to start while a RUNNING run exists (`klai-connector/app/services/scheduler.py:130-142`;
  409 on the route). The engine runs at most 3 syncs at once and one per
  connector (`klai-connector/app/services/sync_engine.py:136-137`), checkpoints
  every 10 documents and can resume (`:438-448` of the same file). At startup it resets RUNNING runs of
  non-delegated connectors to PENDING (`klai-connector/app/main.py:61-89`).
- **Cancel.** The connector has no cancel route. Ingest exposes
  `POST /ingest/v1/crawl/sync/{job_id}/cancel` (`routes/crawl_sync.py:336`) and
  the connector client has a method for it (`klai-connector/app/clients/knowledge_ingest.py:389`)
  that nothing calls.
- **Completion.** An ordinary connector run ends when the document loop ends,
  after the source-deletion step below; the engine writes status, counts and
  skip reasons on the `sync_runs` row. A crawl run does not wait: see 1.4.
- **Credentials.** `klai-libs/connector-credentials` (`store.py`): AES-256-GCM,
  a key-encryption key encrypts a per-org data key
  (`portal_orgs.connector_dek_enc`) which encrypts
  `portal_connectors.encrypted_credentials`. Ingest decrypts crawl cookies only
  when a crawl task runs (`crawl_tasks.py:60-77`, `connector_cookies.py`); the
  sync request carries the connector id and no secret (`routes/crawl_sync.py:62-70`).
- **Not an adapter.** `web_crawler` has no adapter in the connector
  (`klai-connector/app/main.py:107-108`).

### 1.4 Web crawling

The connector's `_run_web_crawler_delegation` posts `POST /ingest/v1/crawl/sync`
(`connector_id`, `generation`, org, KB, `base_url`, `max_pages` 200 by default
and 1 to 10000, `path_prefix`, `content_selector`, canary and login-indicator
settings, `max_depth` 3 by default and 1 to 10, `discovery_seed_url`), stores
the returned `remote_job_id` on the sync run and returns
(`klai-connector/app/services/sync_engine.py:773-942`,
`routes/crawl_sync.py:62-100,197-305`). The state of the run is resolved when it
is read (`SyncRunResolver`) and by a reaper that ticks every 60 s, finalises
RUNNING rows older than 5 minutes and force-fails after 7 days
(`klai-connector/app/services/sync_run_reaper.py:62-104`).

The crawl is the Procrastinate task `run_crawl` on queue `crawl-jobs`
(`crawl_tasks.py:37-47`). It retries without limit on `CancelledError` and
`CrawlExecutionBusy`, 5 s apart, and is fenced by an execution generation with
Postgres checkpoints and a frontier table (`crawl_checkpoint.py`, migrations
0011 and 0012), so a restarted worker resumes where the previous one stopped.

What the crawler does:

- **Discovery and fetch.** Sitemap and sitemap-index discovery
  (`crawl4ai_client.py:137`) and a deterministic frontier with depth
  (`crawl4ai_client.py:1737-1800`). One crawl4ai request per URL, one request
  per second per host by default (`host_pacing.py:1-10`,
  `routes/crawl_sync.py:268`); congestion halves the rate down to 0.2 per second
  and clean crawls raise it again (`domain_rate_limit_control.py:80`,
  `config.py:144-176`). A host circuit breaker stops fetching a failing host
  (`host_circuit_breaker.py`). `host_pacing.py:27-60` refuses to start with more
  than one uvicorn worker, because the pacing registry is in memory.
- **Extraction.** The crawl configuration carries no page JavaScript:
  crawl4ai 0.9.5 answers HTTP 400 to `wait_for` and `js_code`
  (`crawl4ai_config.py:10-16`; the contract is tested by
  `deploy/tests/crawl4ai-config-contract.sh`). `PruningContentFilter` at 0.45;
  without a stored selector the page chrome (navigation roles, header, footer,
  aside) is excluded, with a selector only that element is targeted
  (`crawl4ai_config.py:30-70`). Selectors are stored per `(domain, org_id)` in
  `knowledge.crawl_domains` (`selector_source` user or ai); a user selector
  always wins. AI selector detection (`klai-fast`, when a preview has under 100
  words) runs only in the wizard preview (`routes/crawl.py:191,512`), never in a
  scheduled crawl.
- **Audit before publish.** After fetching and before the first write of any
  kind, `_judge_run_before_write` (`adapters/crawler.py`) decides whether the
  run may write at all. Three verdicts write nothing (no page, link-graph row,
  `crawled_pages` row, stale purge or follow-up job) and end the job
  `failed_partial`: saved cookies and any walled page (`auth_wall_detected`);
  an anonymous run walled at the existing dirty-content rate
  (`boilerplate_or_authwall_dominant`); and a content regression
  (`content_regression`), judged against the stored text of each page's active
  version (`artifacts.extra.document_text`, read in one query by
  `pg_store.get_active_document_texts`). A page lost content when it keeps
  under 90% of its stored words and at least 50 stored words are gone
  (`content_retention.retention`); the run regresses when at least 10 pages
  changed and at least half of them lost content. On production history
  (2026-11-12) this flags 4 of 116 runs, all logged out. The connector shows
  the reason as a failed sync. `accept_content_regression` on
  `POST /ingest/v1/crawl/sync` is a person's acceptance of a real site change:
  the run writes and no page of it is held. It is a request parameter only:
  nothing stores it, so it cannot switch the check off for later runs. Template-cluster walls
  only show per page during the ingest; they still only set the job status.
- **Link graph before ingest.** `_build_link_graph` runs before the per-page
  ingest (`adapters/crawler.py:1273,1305`) so link data exists when pages are
  written. `links_to` (at most 20), `anchor_texts` and `incoming_link_count`
  are written per page (`adapters/crawler.py:2009-2013`).
- **Dedup.** `crawled_pages` keeps `raw_html_hash`, `content_hash`,
  `raw_markdown` and `content_simhash`. An unchanged raw hash skips the page; a
  changed raw hash with an unchanged content hash updates the raw hash only;
  both changed means a full re-ingest (`adapters/crawler.py:1727-1745`). The
  hashes of a job are read in one batch query (`pg_store.py:1609`).
- **Authenticated crawls.** Cookies are applied on every request. A canary URL
  and a `login_indicator` selector are checked against fetched pages; a page
  that matches an auth wall fails the run instead of being ingested. Login-wall
  detection for anonymous crawls is on by default in `reject` mode
  (`utils/auth_wall_detector.py`, `deploy/docker-compose.yml:2259-2260`). The
  connector's `SessionKeepAlive` calls `POST /ingest/v1/crawl/keep-alive` every
  1800 s for scheduled crawlers with saved credentials
  (`klai-connector/app/services/session_keepalive.py:24`,
  `routes/crawl_sync.py:759`).
- **Outcomes.** `crawl_site` returns results and per-URL outcomes
  (`crawl4ai_client.py:1683-1694`), stored as `fetch_outcomes`. Reason codes are
  in `reason_codes.py:25-60`. Alerts and triage: [crawl-failures.md](../runbooks/crawl-failures.md).
- **Stale pages.** After a complete crawl the pages the site no longer serves
  are purged (see 4.2).
- **After the crawl.** Crawl completion enqueues a taxonomy backfill
  (`adapters/crawler.py:1634`).

### 1.5 File upload

The portal endpoint is `POST /api/app/knowledge-bases/{kb}/sources/file`
(`klai-portal/backend/app/api/app_knowledge_sources.py:396,698-760`): at most 10
files per request.

- `.md`, `.txt`, `.csv` (10 MB) are forwarded directly to
  `/ingest/v1/document`.
- `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.json`, `.xml` (200 MB, magic bytes
  checked) are submitted to docling-serve `POST /v1/chunk/hybrid/file/async`
  with `include_converted_doc=false` and `convert_image_export_mode=placeholder`
  (`klai-portal/backend/app/services/docling_client.py:117,159-222`). `kb_upload_poller` (every 5 s,
  `klai-portal/backend/app/services/kb_upload_poller.py:48`) polls `/v1/status/poll/{task_id}`, fetches
  `/v1/result/{task_id}` and posts to ingest with `skip_chunking=true` and
  `chunks=[docling chunks]`. `content` is a preview of at most 450,000
  characters; the ingest request model caps `content` at 500,000
  (`models.py:23`).
- `.zip` and `.tar` are extracted by `klai-portal/backend/app/services/archive.py` (live, called from
  `klai-portal/backend/app/api/app_knowledge_sources.py:604`) and every member goes through the same
  dispatcher. The guards (200 members, 10 converted members, 50 MB per entry,
  500 MB in total, compression ratio 10) are the constants at
  `klai-portal/backend/app/services/archive.py:60-65`. A member with an unsupported type rejects the
  whole archive.
- `.doc` is `phase_pending` and not processed (`klai-portal/backend/app/services/file_upload.py:60-70`).

The portal sends `content_type="document"` for uploads
(`klai-portal/backend/app/services/kb_upload_poller.py:273`), which is not a profile name and falls back
to the `unknown` profile (`content_profiles.py:103-105`). The operator view is
in [kb-file-upload.md](../runbooks/kb-file-upload.md).

### 1.6 Transcripts, MCP and partner writes

Scribe sends the full transcript text, not a summary
(`klai-scribe/scribe-api/app/services/knowledge_adapter.py:22-97`): chunks of
about four speaker turns and at most 400 tokens, `skip_chunking=true`,
`synthesis_depth=0`. The `content_type` is `meeting_transcript` by default and
`1on1_transcript` for recordings. The speech-to-text endpoint is configured as
`whisper_server_url` in scribe's configuration (`klai-scribe/scribe-api/app/core/config.py:10-40`)
and points at the transcription service of 6.2.  MCP saves use `kb_slug=personal-<verified user>` for personal
knowledge and `org` for the organisation's (`klai-knowledge-mcp/main.py:915-925`).

Saving a Scribe transcript into knowledge is a separate request, not a step of
transcription: the route takes the target `kb_slug` in its body and loads only a
transcription owned by the caller (`klai-scribe/scribe-api/app/api/transcribe.py:460-502`),
`ingest_scribe_transcript` has no other caller than that route, and the portal
frontend's transcription screens contain no call to it and no knowledge-base
picker (`klai-portal/frontend/src/routes/app/transcribe/`). Checked on
12 November 2026; the logs of scribe-api carry no request paths, so how often the
route is used cannot be counted from them.

E-mail and pasted correspondence are not ingest sources. No route of 1.1 reads a
mailbox or takes an e-mail file type. The portal's IMAP listener polls one mailbox
for calendar invites that schedule meeting bots, and reads nothing else from the
mail (`klai-portal/backend/app/services/imap_listener.py:1-6`). Correspondence
pasted into a chat is recognised by marker lines such as header blocks and
forward markers and changes how that chat turn is answered
(`klai-portal/backend/app/services/pasted_correspondence.py:1-30`); the e-mail
threads of a HubSpot support connector go to the portal's support cases
(see above), not to ingest.

### Source deletions

After a listing the connector vouches for as complete, it reads every
`source_ref` ingest stores for that connector
(`GET /ingest/v1/connector/source-refs`, keyset-paginated, default page 1000,
maximum 5000, scoped to org, KB and connector, rows of every version;
`routes/stats.py:158-181`) and diffs it against the listing. A stored ref the
source no longer lists was deleted, trashed or unshared there and is removed
through `DELETE /ingest/v1/connector/document` (`routes/ingest.py:1410-1444`),
which purges all versions (Part 4). The route returns each ref with its stored
paths, and a ref is gone only when its path is not the path of any listed
document either: ingest matches rows by path, so an unchanged document that
comes back under a new ref (a branch switch, a renamed repository) keeps its old
ref and is never deleted. The diff is against the store, not the previous run,
so a deletion missed earlier is found again on the next run
(`klai-connector/app/services/sync_engine.py:500-640`).

Deletion happens only when the adapter says the listing is complete
(`BaseAdapter.listing_is_complete`, default `False`). The engine clears that
answer before every listing (`forget_listing`, `klai-connector/app/services/sync_engine.py:332-334`), so a
code path that records nothing cannot inherit an earlier "complete". The
guards:

- a listing that raised fails the run and deletes nothing;
- a listing cut short by a cap, or truncated by the source, deletes nothing;
- one failed document in the run blocks every delete;
- an empty listing while the store holds refs is refused, with warning
  `stale_cleanup_refused_empty`;
- a failed delete call does not fail the run; the ref stays stored and the next
  run retries it (`stale_cleanup_delete_failed`);
- if the store read fails, nothing is deleted (`stale_cleanup_store_unavailable`);
- an adapter may veto a candidate through `confirm_gone` (Notion search
  listings do: the page is retrieved and deleted only on `object_not_found`, a
  trash flag, or a page outside the configured database filter).

A document outside a narrowed connector filter counts as gone; one inside never
does. Each run logs one `source_deletions` event (`listing_complete`, `listed`,
`known`, `removed`, `kept`, `missing_kept`, `delete_failed`, `held_back`,
`documents_failed`), and a held-back deletion adds `stale_cleanup_held_back`
with the reason to `error_details`. There is no percentage cap in the
connector.

| Connector | Listing | Deletes? | Why not |
|---|---|---|---|
| json_feed | whole feed | yes | |
| notion (search) | all pages via search, filtered on `database_ids` | yes | `max_pages` (500) reached (`adapters/notion.py:124,339-341`) |
| notion (`page_ids`) | each page retrieved | yes | |
| github | recursive git tree | yes | tree `truncated` (`adapters/github.py:172`) |
| confluence | pages with `status=current` per space | yes | a space or the space discovery hit the cap of 100, or a space page listing was incomplete (`adapters/confluence.py:63,373`) |
| airtable | every record of each table | yes | |
| google_drive, folder or file selection | `files.list` with `trashed = false`, or `files.get` | yes | `_DEFAULT_MAX_FILES` (500) cut the listing or Drive answered `incompleteSearch` (`adapters/google_drive.py:89,353-357`) |
| google_drive, whole drive | change feed | no | incremental |
| ms_docs, pinned items | each item fetched | yes | |
| ms_docs, drive, site or folder | Graph delta after the first run | no | `delta_mode` (`adapters/ms_docs.py:250,256`) |
| web_crawler | delegated to ingest | own stale-page handling (4.2) | |

A Notion `database_ids` entry is whatever was pasted into the portal, so
`_extract_config` reduces every entry (a link, a dashed or an undashed id) to
the dashed id that Notion reports as `parent.database_id`, and raises when an
entry holds no Notion id (`adapters/notion.py:53-63,123`). Before the search,
`list_documents` retrieves each filtered database (`_require_databases`,
`adapters/notion.py:176-190`, called at `:332`), so a filter that names a page,
or a database not shared with the integration, fails the sync with Notion's
error instead of completing with zero documents (#2340). A connector that holds
`page_ids` and an unusable `database_ids` entry now fails as well, because the
entry is reduced before the pinned pages are looked at.

The first complete sync of a connector after this logic also removes documents
that were deleted at the source before it, because the store still holds them.
The GitHub tree-SHA skip is not taken when `path_filter` changed.

## Part 2: Inside the ingest service

Everything a source sends ends in the pipeline of 2.1, except a crawl, which
first runs the two-phase order of 1.4 (link graph built before the per-page
ingest).

### 2.1 The synchronous part

`POST /ingest/v1/document` (`routes/ingest.py:1083`) calls `ingest_document`
(`routes/ingest.py:366`) and answers when the raw points are written. The model
alias and timeout of every call are in the table; `klai-ingest` and `klai-fast`
are LiteLLM aliases (6.1).

| # | Phase | What happens | Model or service, timeout | On failure |
|---|---|---|---|---|
| 0 | Guard | NUL characters are stripped. With `source_connector_id`, `connector_is_active` must hold, else the result is `skipped`. A sync run is not tracked here: the derived jobs belong to the document version (2.6) | | `skipped`, nothing written |
| 1 | Unchanged check | sha256 of the normalised text (or the caller's `content_hash`) against the active row; equal means `skipped / content unchanged`, with at most a version-gated graph refresh (2.5) (`routes/ingest.py:443-480`) | | |
| 1b | Shrink hold | only with `source_connector_id` (crawls and connector syncs; uploads, transcripts, docs and MCP writes never): a new version that keeps under half of the active version's `document_text` words is `held` (reason `shrunken_version_held`). The candidate hash and first sighting go into the stored row's `extra.held_candidate`; nothing else is written and no model work starts. The same candidate seen again at least 12 hours later replaces the stored version; a different one restarts the hold. A held crawl page keeps its `crawled_pages` hashes, so the next crawl offers it again; a connector sync counts it under `skip_reasons` (`_hold_shrunken_version`) | | |
| 2 | Chunk | see 2.2; empty means `skipped / empty document` (`routes/ingest.py:481-530`) | | |
| 3 | Dense embedding | `embedder.embed`, batches of 32 | TEI `/v1/embeddings`, bge-m3, 1024 dimensions, `tei_timeout` 120 s, 5 attempts with jittered backoff up to 30 s (`embedder.py:15-110`, `config.py:12`) | raises; no artifact row exists yet, so the request fails |
| 4 | Content label | blind label of the text | `klai-ingest`, 15 s (`content_labeler.py`, `config.py:435`) | any error gives `[]`; a spent key or budget gives "not yet classified" and the hourly sweep fills it |
| 5 | Taxonomy | only if the KB has taxonomy nodes: centroid classification of `embed([content[:512]])` at threshold 0.85, otherwise an LLM call | `klai-ingest`, 30 s (`config.py:433-438`) | centroid error: warning, then the LLM path; capacity error: `taxonomy_node_ids=None` |
| 6 | Artifact row | one transaction: close the active row(s) of this path or of the same `(source_connector_id, source_ref)` (`belief_time_end=now`), create the new row with `index_status='pending'` and `extra` including `document_text`, set `superseded_by` (`routes/ingest.py:672-702`, `pg_store.py:401-496`) | | |
| 7 | Rename cleanup | for a closed row under another path: delete the Qdrant points of the old path and repoint the graph episodes to the new document name (`routes/ingest.py:704-780`) | | both are logged at error level and swallowed |
| 8 | Images and parents | `artifact_images` refs from `extra.image_urls`, `parent_chunks` rows (`routes/ingest.py:787-825`) | | image refs: logged, ingest continues; parents raise |
| 9 | Payload | adapter extra, frontmatter, `source_label`, `kb_name`, `connector_type`, `source_domain`, the cheap layer (2.3) and `visibility`, which is read from `kb_config` and set last (`routes/ingest.py:830-900`, `kb_config.py:134-215`) | | |
| 10 | Raw upsert | `qdrant_store.upsert_chunks`: dense `vector_chunk` and sparse `vector_sparse` per chunk; payload includes `text`, `chunk_index`, `parent_chunk_id`, `heading_path`, `text_fingerprint`, `quality_score=0.5`, `feedback_count=0`. `_replace_points` snapshots the point ids of `(organisation, kb, path)`, upserts the new points, then deletes only the snapshotted ids (`qdrant_store.py:241-362`) | sparse service `POST /embed_sparse_batch`, batches of 64, 30 s in compose (code default 5 s) (`sparse_embedder.py:18-60`) | sparse failure writes dense-only points and never raises; a Qdrant error raises |
| 11 | Persist extra | `update_artifact_extra`, so a worker can rebuild everything from the artifact id (`routes/ingest.py:931`) | | |
| 12 | Enrichment decision | only `too_many_chunks` skips (more than 2000 chunks, counting `docling_chunk_count`) and sets `llm_enrichment_skipped`; otherwise, if the organisation has enrichment enabled, defer `enrich_document_interactive` (a direct file upload) or `enrich_document_bulk` with `queueing_lock={org_id}:{kb}:{path}:{artifact}` (`routes/ingest.py:890-974`, `enrichment_policy.py:20-72,98-118`) | | `AlreadyEnqueued` is swallowed |
| 13 | Status | `index_status='synced'`, except that a direct upload awaiting interactive enrichment stays `pending` (`routes/ingest.py:975-985`) | | raises if no row was updated |
| 14 | Graph job | see 2.5 (`routes/ingest.py:988-1067`) | | a cleanup error is logged and ingest still succeeds |

### 2.2 Chunking and content profiles

Callers that do not supply chunks go through `normalize_document_for_chunking`
and `chunker.chunk_markdown_with_parents` (`chunker.py:205-251,273-353,374-436`):
sections split at H1 to H3; link-only sections (link density at least 0.5,
`chunker.py:245`) are dropped; sections under 50 tokens are merged into a
neighbour (`chunker.py:251`); a parent is a section or a block of 6000
characters (`chunker.py:224`); children are cut at the profile size without
overlap and keep paragraph boundaries.

The child size is `chunk_tokens_max * 4` characters
(`content_profiles.py:26-100,108-110`):

| `content_type` | Child chars | Question vectors (HyPE) | Context strategy |
|---|---|---|---|
| `kb_article` | 1000 | when `synthesis_depth <= 1` | `first_n` |
| `email_thread` | 1000 | when `synthesis_depth <= 1` | `most_recent` |
| `pdf_document` | 1200 | always | `front_matter` |
| `meeting_transcript` | 1200 | always | `rolling_window` |
| `1on1_transcript` | 1000 | always | `rolling_window` |
| `unknown` and any name that is not a profile | 1000 | never | `first_n` |

The context strategies (`first_n`, `rolling_window`, `front_matter`,
`most_recent`) select the passage of the document handed to the model, and are
used only when no document summary exists (`enrichment.py:405-411`).

Chunks a caller supplies (`skip_chunking=true`, used by the docling upload path
and by scribe) are used as they are for the first write: each chunk is its own
parent, with one `parent_chunks` row per chunk (`routes/ingest.py:493-505`).
Enrichment later re-derives chunks from the stored `document_text` with the
markdown chunker (`enrichment_tasks.py:259`). Only when the indexed points
already hold at least 200 chunks does the enrichment keep the indexed chunking
(`enrichment_tasks.py:279`). A pre-chunked document with fewer chunks is
therefore re-chunked by the markdown chunker once its enrichment succeeds, and
its points afterwards are the markdown chunker's.

### 2.3 Enrichment

`enrich_document_bulk` and `enrich_document_interactive` call `_load_and_enrich`
(`enrichment_tasks.py:178-315`), which re-reads the artifact from Postgres and
aborts when it is gone, superseded or its connector inactive.

- **Batching.** A document is enriched whole, unless the task has a
  `batch_offset` or the indexed points already contain a chunk with index at
  least 200 (`enrichment_max_chunks`, the batch size). Then
  `_enrich_next_batch` enriches 200 indexed chunks at a time and defers the
  next batch on `enrich-bulk` (`enrichment_tasks.py:317-380`). Enrichment is
  never skipped at 200; the ceiling is `enrichment_hard_max_chunks` = 2000
  (`config.py:355-359`), about 56 minutes of the shared `klai-ingest` budget at
  0.6 requests per second for one document.
- **Document summary.** `contextual.generate_document_summary` (language by
  lingua), `klai-ingest`, `max_tokens` 220, 30 s. The summary is reused when the payload already holds one, but nothing writes
  it back to the artifact, so every batch and every retry generates it again. On
  failure it returns an empty string and the chunk prompts fall back to the
  document context window (`enrichment_tasks.py:1017-1034`,
  `contextual.py:60-61,173-230`).
- **Per-chunk call.** One LLM call per chunk returns the context sentence and
  the generated questions: `klai-ingest`, `max_tokens` 300, 15 s, without
  fallback to larger models (`add_no_fallback`). Concurrency is 2 chunks
  (`enrichment_max_concurrent`) and every call takes a token from the shared
  limiter, 0.6 requests per second with a burst of 10
  (`enrichment.py:158-192,367-442`, `llm_throttle.py:73-90`). The prompt also
  receives the KB name, connector type and source domain.
- **Embedded text.** `context_prefix + "\n\n" + chunk`, with the alias and
  anchor lines of the cheap layer first. Dense and sparse embeddings of the same
  text are computed in parallel. `vector_questions` is the embedding of the
  joined questions, or of the chunk text when a chunk has none, and exists only
  when the profile has HyPE enabled for the document's `synthesis_depth`
  (`enrichment_tasks.py:1062-1095`).
- **Write.** The artifact is checked active again; `parent_chunks` are deleted
  and inserted again (whole-document path only; batches keep the parents of the
  base ingest); a fence check; `upsert_enriched_chunks` through `_replace_points`
  adds `text_enriched`, `context_prefix`, `questions`, `ingested_at` and
  `valid_from` / `valid_until`; if the artifact became inactive meanwhile the
  points just written are deleted; `index_status='synced'`
  (`enrichment_tasks.py:1100-1253`). Raw points carry `belief_time_start` and
  `belief_time_end` but not `valid_from` and `valid_until`, which exist only on
  enriched points (`qdrant_store.py:424-429`).
- **Failure.** A transport, HTTP or JSON error from the model raises
  `EnrichmentError` and Procrastinate retries (`enrich_document_bulk`: 5
  attempts, exponential wait 4; `enrich_document_interactive`: 4 attempts,
  exponential wait 3). A failed chunk is never upserted with a substitute
  prefix. A capacity error (402 or 429 "no deployments", a spent key or budget)
  re-defers the job for up to 3 days and the artifact stays `pending`
  (`llm_capacity.py:50-90`, `enrichment_tasks.py:420-465`). Any other exception
  (embedding service, Qdrant, database) is logged as `enrichment_infra_failed`
  and swallowed, and the raw points remain searchable. The status becomes
  `failed` only when the first batch (`chunk_offset` 0) fails and the artifact
  has no source connector (uploads, docs editor, MCP); the same holds for the
  `EnrichmentError` path before Procrastinate retries. A connector or crawl
  document stays `synced`, and so does an artifact whose later batch fails
  (`enrichment_tasks.py:485-500,1203-1253`). A batch that fails permanently
  stops the chain; earlier chunks keep their context, later ones stay raw.

**The cheap layer.** Part of what a chunk stores follows from its text and the
KB's `aliases` and `product_rules`, without a model call: the "Also known as"
line, the anchor line, the `product` label, `text_fingerprint` (a MinHash used
by retrieval to collapse lookalikes), the vectors of the embedded text and
`cheap_layer_version` (`cheap_layer.py:1-45`; anchor rules version 2,
`:40`; anchor limits `anchor_filter.py:27-34,53-59`). Ingest and enrichment
both compute it through `cheap_layer.cheap_fields` (`cheap_layer.py:128-140`;
called from `routes/ingest.py:883` and, through `cheap_layer.apply_to_enriched_chunks`,
from `enrichment_tasks.py:1058`), and `kb_patch` (5.2)
recomputes it for points whose version is out of date. Aliases and product
rules are read from `portal_knowledge_bases` with a 60 s cache
(`kb_config.py:134-215`). The `source_label` of a chunk is the crawl domain, the
connector type, `meetings` for transcripts, or the KB slug (`source_label.py:11-37`).

### 2.4 Images

Images go to object storage under `{org_id}/images/{kb_slug}/{sha256}.{ext}`
(`klai-libs/image-storage/klai_image_storage/storage.py:103-107`), shared by the
crawler (`adapters/crawler.py:664`) and the connector
(`klai-connector/app/services/sync_engine.py:1126`) through
`klai-libs/image-storage/klai_image_storage/pipeline.py:316`. Ingest records the references in
`knowledge.artifact_images` (`routes/ingest.py:787-806`). The portal serves them
at `/kb-images/<key>` after checking the session's organisation
(`deploy/caddy/Caddyfile:325-345`, `klai-libs/image-storage/klai_image_storage/kb_image.py:213`).
The key format and the `/kb-images/` prefix are invariants: the URLs are stored in
`extra.image_urls` and ingest parses the key back out of them (`routes/ingest.py:303-319,786-795`),
so changing either breaks every image URL stored before the change.

### 2.5 Graph extraction

For a document that is not a navigation page (at least 8 links and fewer than
40 prose characters per link, `enrichment_policy.py:graph_episode_skip_reason`),
ingest defers `ingest_graphiti_episode` on `graphiti-bulk` with
`queueing_lock=graphiti:{artifact}` and `replace_stale=True`
(`routes/ingest.py:988-1067`). A navigation page is marked
`graphiti_episode_id="skipped:navigation_page"` and its earlier extractions are
retired.

The task (`enrichment_tasks.py:631-928`, `graph.py:971-1165`):

- splits the text into parts of at most 30,000 characters (`episode_text.py:3`)
  and calls `graph.ingest_episode` per part: graphiti-core `add_episode` on
  FalkorDB (one graph per org through `driver.clone(org_id)`, `graph.py:528`), LLM `klai-ingest`
  (`graphiti_llm_model`), embeddings by TEI bge-m3, every HTTP call taking a
  token from the shared limiter. `graphiti_max_concurrent` is 1 and
  `graphiti_episode_delay` 10 s;
- `ingest_episode` makes three attempts with two waits between them: 65 s * 2^n
  on a rate limit, 30 s * 2^n on 503 or provider errors and 1 s * 2^n otherwise
  (`graph.py:408-423,1005-1009,1141-1152`); the task itself retries 8 times after the first
  run with exponential wait 4, about 24 hours in total
  (`enrichment_tasks.py:60-72`);
- a FalkorDB that dies twice during one document's extraction quarantines that
  build, so it is not retried (`enrichment_tasks.py:72-78`);
- aborts, and deletes the part just written, when the artifact was deleted or
  superseded during the run;
- after the parts: Hebbian reinforcement (`weight += 1` on co-mentioned edges),
  PageRank over `Entity` / `RELATES_TO` (`graph.py:817-842`) written to Qdrant as
  `entity_uuids`, `entity_names` (case-insensitive substring match per chunk,
  minimum length 3, `qdrant_store.py:757-790`) and `entity_pagerank_max`;
- when every part is written and `replace_stale` holds,
  `retire_earlier_extractions` deletes every other episode of the document's
  history (`graph.py:684-710`), so the graph keeps only the newest finished
  extraction per document; the old facts stay until the new extraction is
  complete. Episodes younger than 24 hours that no artifact refers to are not
  swept as orphans (`graph.py:715`);
- records `graphiti_episode_ids`, `graphiti_episode_complete` and
  `graphiti_extraction_version` (2) on the artifact.

An unchanged re-sync or upload of a document whose extraction version is below 2
re-queues the graph job without re-chunking, at most 300 per organisation per
UTC day, with a 7-day cool-off after exhausted retries
(`graph_refresh.py:27-60,141-193`). "Current" in the graph means the newest
finished extraction recorded on the artifact rows
(`pg_store.py:1242-1312`). Taxonomy has no periodic re-clustering task
(`GAP-TAX-01`); classification happens at ingest (phase 5) and in the jobs of 7.

### 2.6 Queues and lanes

Ten queues in four lanes, one Procrastinate worker per lane, each supervised and
restarted with 1 to 60 s backoff (`queues.py:42-121`, `worker.py:207-280`):

| Lane | Concurrency | Queues | Tasks |
|---|---|---|---|
| io | 8 | `ingest-kb`, `connector-purge`, `crawl-jobs` | `ingest_from_gitea`; stale-pending reaper; `connector_purge_task` (5 attempts, waits 60, 300, 1500, 7200, 36000 s); `run_crawl` |
| interactive | 2 | `enrich-interactive` | `enrich_document_interactive` |
| llm | 4 | `enrich-bulk`, `graphiti-bulk`, `rag-eval`, `taxonomy-backfill`, `rebuild-kb` | `enrich_document_bulk`; `ingest_graphiti_episode`; eval tasks; `run_taxonomy_backfill` (one retry, none on a capacity error); `run_auto_categorise` (waits 30, 300, 1800 s); login-wall backfill tasks; `rebuild_kb` (1 attempt) |
| maintenance | 1 | `maintenance` | stalled-job recovery, Postgres-Qdrant reconcile, taxonomy sweep |

`ALL_QUEUES` is the four lists together (`queues.py:119`); a test pins that
every queue constant is in exactly one lane
(`klai-knowledge-ingest/tests/test_queues_constants.py:71`). Jobs live in Postgres
(`procrastinate_jobs`), the pool holds 4 to 20 connections (`worker.py:107-108`),
shutdown gets 20 s of the 90 s compose grace (`worker.py:94`,
`deploy/docker-compose.yml:2188`), and a worker is stalled after 120 s without
heartbeat. `recover_zombie_jobs` runs at start and every minute
(`zombie_recovery.py:30-123`); a zombie whose `queueing_lock` is already held by
a newer todo job is aborted instead of retried.

**Derived work belongs to the document version.** Enrichment and graph jobs
carry only `artifact_id`; a new sync or crawl run never cancels or fences them,
because that run skips unchanged pages and would not queue the work again (in
2026-11 tying them to the run lost 353 enrichment and 387 graph jobs of
~11,300). A job stops only when its version is replaced or deleted, or its
connector is deleting or gone: it then raises procrastinate's `JobAborted`
(status `aborted`, no retry, `outcome=abandoned` in the log,
`enrichment_tasks._abandon`). It checks this at the start and again, fresh,
right before and after each vector write and after each graph part; a lookup it
cannot make raises and the job is retried. Only crawl runs carry a resource key
`(org, kb, connector, generation)` (`resource_jobs.py`, `connector_state.py`): a
new crawl run moves the fence to its generation and cancels the older crawl job
on `crawl-jobs` only (`crawl_sync.start_crawl_run`); crawl execution has its
own generation fence (1.4). A connector delete marks the fences deleting,
cancels crawl jobs by resource key and the enrichment and graph jobs of the
connector's documents by `artifact_id` (`resource_jobs.cancel_jobs_by_artifact_ids`).

### 2.7 Periodic tasks

All in UTC, Procrastinate `@periodic`:

| Schedule | Task | Queue | What it does |
|---|---|---|---|
| every minute | `recover_stalled_jobs_periodic` | maintenance | retries jobs whose worker heartbeat expired (`zombie_recovery.py:125-139`) |
| `*/15 * * * *` | `reap_stale_pending_artifacts_periodic` | ingest-kb | marks artifacts `pending` for over 30 minutes with no runnable job as failed, at most 500 per run (`stale_pending_artifact_reaper.py:25-27,52-66`) |
| `17 * * * *` | `sweep_unclassified_kbs_periodic` | maintenance | queues one taxonomy backfill per KB that still has chunks without label or taxonomy (`taxonomy_tasks.py:34,88-102`) |
| `0 2 * * *` | `evaluate_retrieval_quality_periodic_chat` and `_knowledge_org` | rag-eval | one evaluation run per suite (`eval/ragas_runner.py:604-706`) |
| `30 2 * * *` | `evaluate_ingest_gap_canary_periodic` | rag-eval | probe queries derived from sources (at most 10, 5 minute budget) (`eval/ragas_runner.py:44-48,394-430,651-665`) |
| `30 3 * * *` | `reconcile_pg_qdrant_periodic` | maintenance | read-only diff of active artifacts against Qdrant artifact ids and, with the graph on, of recorded graph episodes against the graph (7.2) |

Not periodic: rebuild, patch, sparse backfill, purge scripts.

### Multilingual chat

Not an ingest topic. The embedding model maps all languages into one space, so
ingest does nothing per language; the chat language behaviour is described in
[chat-system.md](chat-system.md), and its observability in
[multilingual-chat-observability.md](../runbooks/multilingual-chat-observability.md).

### 2.8 Settings and code that look active and are not

- LLM pacing for enrichment, graph, taxonomy and labelling is the one shared
  limiter, `litellm_klai_fast_rps` 0.6 and `litellm_klai_fast_burst` 10
  (`config.py:339-340`, `llm_throttle.py:83-84`); there is no `graphiti_llm_rps`
  setting and the compose file does not set one. `GRAPHITI_EPISODE_DELAY` is
  read (`config.py:392`, `deploy/docker-compose.yml:2227`).
- `knowledge.embedding_queue` is never inserted into; only deleted from
  (`pg_store.py:732,882,995`). The dual-store outbox did not land
  (`GAP-SYNC-01`); consistency is checked by the nightly reconcile (7.2).
- `extra.document_text_truncated`, written by the portal poller
  (`klai-portal/backend/app/services/kb_upload_poller.py:285`), is read by no
  production code. A truncated preview is handled through `docling_chunk_count`
  (`enrichment_policy.py:enrichment_skip_reason`).
- `rebuild_kb` has a comment claiming a `queueing_lock`; its decorator sets none
  (5.1).
- `routes/ingest.py:329` `_graphiti_background` is never called.
  `RetrieveRequest` and `RetrieveResponse` (`models.py:51,72`) are unused:
  ingest has no retrieve route. `compute_incoming_counts` (`link_graph.py:86`)
  and `update_link_counts` (`qdrant_store.py:945`) have no production caller.
- `knowledge_ingest/sync_images.py` and `settings.graphiti_llm_rps` no longer
  exist; image code is in `klai-libs/image-storage`.

## Part 3: Storage written at ingest

The field-level model is in
[klai-knowledge-architecture.md](klai-knowledge-architecture.md); this is the
list of what ingest writes, and by which step.

| Store | What | Written by |
|---|---|---|
| Postgres `knowledge.artifacts` | one row per document version: ids, organisation, KB, path, provenance, assertion mode, `synthesis_depth`, `belief_time_start` / `belief_time_end` (end `253402300800` while active), `superseded_by`, `content_hash`, `index_status` (`pending`, `synced`, `failed`, `deleting`), `extra` JSONB with `document_text`, title, connector provenance, graph episode ids and flags | phases 6, 11, 13; workers |
| `knowledge.parent_chunks` | parent text per artifact (cascade on delete) | phase 8; rewritten by whole-document enrichment |
| `knowledge.artifact_images`, object storage | image references and bytes | phase 8, `klai-libs/image-storage` |
| `knowledge.crawl_jobs`, `fetch_outcomes`, checkpoints, `crawl_job_frontier`, `crawled_pages`, `page_links`, `crawl_domains` | crawl state, link graph, selectors, pacing | crawler (1.4) |
| `knowledge.kb_config`, `org_config` | visibility, enrichment switch (LISTEN/NOTIFY, `app.py:42-45`) | portal and ingest |
| `knowledge.connector_resource_fences`, `graph_refresh_budget`, `rag_eval_results` | generation fences, refresh budget, evaluation rows | phase 0, 2.5, 7.1 |
| Qdrant `klai_knowledge` | one shared collection; dense `vector_chunk` and `vector_questions` (1024, cosine), sparse `vector_sparse`; keyword payload indexes include `org_id` (created as the partition key for new collections, `qdrant_store.py:94-125`) and `source_label` (`:145`); payload as in phases 9, 10 and 2.3 | phases 10 and 2.3 |
| FalkorDB | one graph per organisation: episodes, entities, `RELATES_TO` facts with an `episodes` list | 2.5 |
| Portal tables ingest reads | `portal_knowledge_bases.aliases` and `product_rules`, `portal_connectors` (state, credentials), taxonomy nodes | portal |

`document_text` is stored in `artifacts.extra` at every ingest
(`routes/ingest.py:660-666,831-838`); it is a JSONB key, not a column. The
summary, `document_text` and `document_language` are deliberately not in Qdrant
(`qdrant_store.py:205-210`). Postgres is the retry ledger for deletes, and
Qdrant is compared against it nightly (7.2).

## Part 4: Change and delete

### Replaced keeps history, deleted is gone

**A replaced document keeps its history.** The old artifact row is closed
(`belief_time_end`), linked to the new row by `superseded_by`, and keeps its
`document_text`. Qdrant gets the new points first and then loses only the ids
snapshotted before the write (`qdrant_store.py:241-278`). A page renamed at the
source is matched by `(source_connector_id, source_ref)`; the old path's points
are deleted and the graph episodes are repointed
(`pg_store.py:401-440`, `routes/ingest.py:704-780`). Graph facts of the old
extraction are deleted only after the new extraction is complete.

**A deleted document is gone.** Every single-document delete runs
`document_purge.purge_documents` (`document_purge.py:30-47`):

1. close the active rows and set `index_status='deleting'`, so search stops
   returning them and enrichment and graph workers abort;
2. walk `superseded_by` to every version (cycle-safe, `pg_store.py:451-470`);
3. delete the graph episodes of all versions;
4. delete Qdrant points by `artifact_id`, and by path for `(kb, path)` pairs no
   live row uses (`qdrant_store.py:544-569`);
5. hard-delete the rows: `embedding_queue`, `artifact_entities`, `derivations`,
   `crawled_pages` and `page_links` of those paths, then `artifacts`;
   `parent_chunks` and `artifact_images` follow by cascade
   (`pg_store.py:702-770`). Every statement is bound to the caller's `org_id`.

When an external store refuses, the request fails and the rows stay closed, so
a repeated delete finds the same ids. An enrichment or graph job that writes
after its artifact was deleted removes what it wrote.

| Event | Function that runs | Stores cleared |
|---|---|---|
| Upload deleted in the portal | `DELETE /knowledge/v1/kb/{kb}/uploads/{id}`, `purge_documents` (`routes/kb_sources.py:356-402`) | Postgres, Qdrant, FalkorDB |
| Personal item deleted | `DELETE /knowledge/v1/personal/items/{id}`, `purge_documents` (`routes/personal.py:60-95`) | same |
| Docs page removed in Gitea | webhook, `_purge_removed_paths` (`routes/ingest.py:1263-1291`); a failing path answers 502 so the push is redelivered | same |
| Document gone at a connector | `DELETE /ingest/v1/connector/document` (`routes/ingest.py:1410-1444`) | same |
| Scribe transcript deleted | `DELETE /ingest/v1/document?org_id&path=scribe/<id>` with caller service `scribe`, across all KBs of the organisation (`routes/ingest.py:1446-1482`) | same |
| Crawled page gone | `adapters/crawler.py` stale purge (4.2) | same |
| Reindex | `POST /knowledge/v1/artifacts/{id}/reindex` sets `pending` and defers `enrich_document_interactive` (`queueing_lock reindex:{org_id}:{id}`); it may reopen a closed head without successor and never a replaced version or a `deleting` row (`routes/kb_sources.py:243-325`, `pg_store.py:2204-2263`) | none |
| Connector deleted | `connector_purge_task` to `purge_connector` (`connector_cleanup.py:150-360`): artifact ids captured, resource fences marked deleting, crawl jobs cancelled by resource key and the documents' enrichment and graph jobs by artifact id, Postgres artifacts and crawl rows deleted, graph episodes deleted, Qdrant points deleted by `source_connector_id`, orphaned image keys deleted from object storage, then a janitor pass; finally a callback to the portal (`portal_client.py:203-230`). It does not use `purge_documents` | Postgres, Qdrant, FalkorDB, object storage |
| KB deleted | `DELETE /ingest/v1/kb` (`routes/ingest.py:1351-1376`): graph episodes of the KB, `qdrant_store.delete_kb`, the KB's images in object storage when an image store is configured (`ImageStore.delete_images(org_id, kb_slug)`, `klai-libs/image-storage/klai_image_storage/storage.py:176-205`), and last `pg_store.delete_kb` (artifacts and their aux tables including `embedding_queue`, `kb_config`, `crawl_jobs`, `crawled_pages`, `page_links` and the KB's `connector_resource_fences`; `pg_store.py:862-934`; `knowledge.entities` are organisation-wide and stay). The rows go last, so when an earlier store raises the portal keeps its KB and retries | Postgres, Qdrant, FalkorDB, object storage |
| Organisation deprovisioned | portal steps (`klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:285-468`): Qdrant points by `org_id`, then ingest `wipe-graph` and `wipe-postgres` (`routes/internal.py:72-182`; one transaction that deletes the org's `embedding_queue` rows (found through its artifacts), `page_links`, `crawled_pages`, `crawl_jobs`, `crawl_domains`, `kb_config`, `org_config`, `connector_resource_fences`, `graph_refresh_budget`, `entities` and `artifacts` rows, the last with the rows that cascade from it), then the step `_delete_kb_images`, which deletes everything under the organisation's `{org_id}/images/` prefix in object storage (`deprovisioning_steps.py:449-468`) | Postgres, Qdrant, FalkorDB, object storage |

Scripts for documents deleted before `purge_documents` existed:
`klai-knowledge-ingest/scripts/purge_deleted_documents.py` (reports closed-but-never-purged
documents; dry run by default, `--execute`, per org) and
`klai-knowledge-ingest/scripts/prune_stale_graph.py` (removes replaced extractions that are still in
the graph; dry run by default, `--execute`, per org; refuses when a document
would end with no graph content).

### 4.2 Stale pages after a crawl

A crawl that completed with `pages_done > 0` and fetched everything it should
(`_crawl_fully_fetched`, `adapters/crawler.py:531`) purges the connector's
active artifacts whose URL it did not crawl, through `purge_documents`
(`adapters/crawler.py:857-893,1500-1580`). If the crawl saw 404 or 410 pages and
the stale set is larger than max(5, 20% of the stored pages), the cleanup is
skipped and logged (`adapters/crawler.py:126-139`), and the next clean crawl
retires what is really gone. The purge also finishes earlier half-finished
purges (`index_status='deleting'`).

## Part 5: Operations

On production, every operation in this part that rewrites knowledge data
(rebuild, backfill with `--execute`, a crawl start, enrichment jobs) follows the
approval order in the section "production knowledge data is rewritten only with
approval per run" of [`AGENTS.md`](../../AGENTS.md); it is not repeated here.

### 5.1 `rebuild_kb`

For every active artifact of one KB (concurrency 4): take `document_text` from
`extra` (if absent, reconstruct it from the KB's Qdrant chunks of that path, in
chunk order), re-chunk, delete the parents, and run the full `_enrich_document`
in one call per artifact (summary, per-chunk model call, embeddings, Qdrant
replace), without 200-chunk batching (`rebuild_tasks.py:85-92,225-330`).
Models are `klai-ingest` throughout. An artifact with neither text source is
skipped (`rebuild_skip_no_text`); one that fails is counted and does not stop the
rest. The entry is `rebuild_kb_inline(org, kb)` or the task `rebuild_kb` on
queue `rebuild-kb`; no route or script defers the task, and `get_app()` raises
in a fresh process (`enrichment_tasks.py:99-104`), so the inline function is
the way to run it. In code there is no dry run, no flag and no `queueing_lock`
(`max_attempts=1`); the guard is the approval order quoted above. A changed alias or product rule does not need a rebuild: use
`kb_patch`. Procedure: [rebuild-kb.md](../runbooks/rebuild-kb.md).

### 5.2 Patch, backfill, reconcile, repair

| Operation | Entry | What it does | Guard |
|---|---|---|---|
| `kb_patch` | `python -m knowledge_ingest.scripts.kb_patch --org [--kb] [--only aliases\|products] [--execute]` | recomputes the cheap layer (2.3) for points whose `cheap_layer_version` differs; batches of 200; no model call | dry run by default; `--execute` is refused unless `INGEST_OK=1` (`klai-knowledge-ingest/knowledge_ingest/scripts/kb_patch.py:285-291`) |
| `sparse_backfill` | `python -m knowledge_ingest.scripts.sparse_backfill --org [--kb] [--execute]` | adds missing `vector_sparse` only | dry run by default |
| `requeue_failed_artifacts` | `--dry-run` or `--execute [--org --kb --limit]` | re-defers `enrich_document_bulk` for active artifacts with `index_status = 'failed'`, from chunk 0, oldest first (`klai-knowledge-ingest/knowledge_ingest/scripts/requeue_failed_artifacts.py:24-27,64-80`); it does not find a connector document or a later batch that failed, because those never get `failed` (2.3) | dry run by default |
| `repair_pg_qdrant_drift` | `<artifact_id>... [--execute]` | per artifact: reindex, retire, delete points or skip, decided from the current state | dry run by default |
| `reextract_document_graph` | `python -m scripts.reextract_document_graph --org --artifact [--execute]` in a knowledge-ingest container (`klai-knowledge-ingest/scripts/reextract_document_graph.py:24-28`) | repairs one document whose finished graph extraction lost episodes: keeps the surviving episode ids, marks the extraction not complete with version 0 and queues the normal graph job as a user-requested refresh, without the daily cap or cool-off (`:97-117`; `graph_refresh.py:61-62`) | dry run by default; refuses (exit 2) a document that is not found, not active, not finished, has no missing episode or no stored `document_text` (`:55-90`); when no job is queued the row is written back exactly as it was and the command exits 1 (`:123-134`); nothing runs it automatically |
| graph `backfill` | `python -m knowledge_ingest.backfill --org-id [--kb-slug] [--limit]` | builds graph episodes for existing artifacts (`backfill.py:1-30`) | flags only |
| `backfill_crawl_source_identity` | `--apply` | fills `source_domain` and `source_label` | dry run unless `--apply` |
| login-wall tasks | `backfill_detect_login_walls_task`, `recover_purged_pages_task` | scan crawled pages and purge login stubs | operator-triggered |
| `purge_deleted_documents`, `prune_stale_graph` | see the delete section | | dry run by default |
| `migrate_collection` | one-time script, the earlier collection layout to the current one | | runs for real unless `--dry-run` is passed, which only lists the artifacts |

`migrate_collection` has no default dry run: `--dry-run` is a switch that only lists artifacts (`klai-knowledge-ingest/knowledge_ingest/scripts/migrate_collection.py:177`).

Only `kb_patch` checks `INGEST_OK` inside the code. The reconcile
(`consistency_reconcile.py`) is read-only and nightly (7.2).

## Part 6: Inference services ingest depends on

### 6.1 LiteLLM aliases

Defined in `deploy/litellm/config.yaml`:

| Alias | Resolves to | Used by ingest for |
|---|---|---|
| `klai-ingest` | `mistral/mistral-vibe-cli-fast`, one deployment, no fallback, budget 20 USD per day, 900 rpm and 450k tpm (`deploy/litellm/config.yaml:300-318`) | enrichment, document summary, content label, taxonomy classification, description and proposals, graph extraction, default RAG-eval judge (`config.py:350,390,433,552`) |
| `klai-fast` | `mistral/mistral-small-2603` (orders 1 and 2), vibe-cli-fast (order 3) | calls a user waits on: crawl preview and auth-probe selector detection, taxonomy bootstrap naming, the portal's classify call (`config.py:345`, `selector_ai.py:58`, `proposal_generator.py:300`) |
| `klai-medium` | `mistral/mistral-medium-3.5` | RAG-eval faithfulness judge only (`config.py:553`) |
| `klai-bge-m3` | `openai/bge-m3` served by TEI | RAG-eval embeddings (`config.py:554`) |

### 6.2 Services called directly

Defined in `deploy/docker-compose.gpu.yml` (the GPU host); the core-01 services
reach them at the docker bridge gateway address given by `TEI_URL` and
`SPARSE_SIDECAR_URL` (`deploy/docker-compose.yml:2208-2209`).

| Role | Compose service | Image or model | Port | Used by |
|---|---|---|---|---|
| Dense embeddings | `tei` (`deploy/docker-compose.gpu.yml:11`) | TEI 1.9, BAAI/bge-m3 | 7997 | ingest, retrieval, LiteLLM `klai-bge-m3`, the graph embedder |
| Sparse embeddings | `bge-m3-sparse` (`:69`, built from `deploy/bge-m3-sparse`) | BGE-M3 lexical weights through FlagEmbedding, `POST /embed_sparse_batch` (`deploy/bge-m3-sparse/main.py:67`) | 8001 | ingest, retrieval (query side) |
| Reranker | `infinity` (`:37`) | infinity 0.0.77, BAAI/bge-reranker-v2-m3 | 7998 | retrieval-api; ingest does not call it |
| Transcription | `transcription-worker-1`, `-2` (`:110,146`) behind an nginx `transcription-api` (`:181`) | vexaai/transcription-service 0.10.6, large-v3-turbo | 8000 | scribe-api |

On core-01 (`deploy/docker-compose.yml`): `docling-serve` v1.38.2 (`:1577`,
internal network only, single-use results off, removal delay 86400 s),
`crawl4ai` 0.9.5 (`:2374`), `qdrant` v1.20.0 (`:2120`), `falkordb` 6.0.4
(`:2142`, query timeout 5000), `garage` v2.5.0 (`:2276`), `knowledge-ingest`,
`klai-connector` (`:2303`), `gitea`, `docs-app`, `klai-knowledge-mcp`,
`scribe-api` and `retrieval-api`. The compose file sets
`SPARSE_SIDECAR_TIMEOUT=30` (code default 5 s) and `GRAPH_ANN_ENABLED=true`
(candidate search on FalkorDB vector indexes, falling back to a scan per query).

## Part 7: Quality measurement inside ingest

### 7.1 The eval package

`knowledge_ingest/eval/`: `ragas_runner.run_evaluation(suite)` loads
`<RAG_EVAL_SUITES_DIR>/<suite>.yaml` (the repository ships only
`eval/suites/_sample.yaml`; compose mounts the real suites read-only and a
missing file fails the nightly run), calls retrieval, runs judge calls through
LiteLLM (`klai-ingest` for light metrics, `klai-medium` for faithfulness) and
writes `knowledge.rag_eval_results`. CLI:
`python -m knowledge_ingest.eval --suite --variant` (`eval/__main__.py:1-45`).
Alerts are in `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml`
(`rag_eval_faithfulness_low`, `rag_eval_canary_dropped`). Results and the
decisions taken from them are in the knowledge-base log.

What runs nightly (UTC): the suites `chat` and `knowledge_org` at 02:00, the
ingest-gap canary at 02:30 and the reconcile at 03:30 (2.7).

What the ingest-gap canary checks: whether the gap detector still sees the
difference between knowledge that is present and knowledge that is missing.
It asks portal-api for the org-owned knowledge bases of ready orgs that have
full telemetry and the `knowledge_gaps` feature unlocked
(`GET /internal/ingest-gap-eval/scopes`, `klai-portal/backend/app/api/internal.py:2676-2699`), picks
at most 10 stored chunks that carry source-derived questions and no `user_id`, round-robin over those
knowledge bases and rotated by the day (`eval/ragas_runner.py:394-470`), and
posts them to `POST /internal/ingest-gap-eval/{org}/{kb}` (`internal.py:2702-2760`).
There portal-api runs each question twice through the support-case analysis: once with the
source chunk as the only retrieved passage, which has to diagnose `covered` or `findability`, and once with
nothing retrieved. A second run that diagnoses `missing` is recorded as `detected_missing`; one that still
claims the answer is present is `withheld_claimed_present`; the rest are `unscorable` or `failed`. Any `failed`
or `withheld_claimed_present` makes the run `failed`, and an `unscorable` result or no scored result makes it
`inconclusive` (`klai-portal/backend/app/services/ingest_gap_evaluation.py:10-11,53-115`).
The rows go to `knowledge.rag_eval_results` under the suite `ingest_gap_canary`
with hashes and ids and no text (`ragas_runner.py:376-391`, variant `source_derived_v1`).

### 7.2 Reconcile and taxonomy jobs

`reconcile_pg_qdrant_periodic` diffs active artifacts against the artifact ids
in Qdrant, tolerating writes from the last 15 minutes, with a 900 s timeout, and
logs the event `pg_qdrant_reconcile` with `missing_in_qdrant` and
`orphaned_in_qdrant` (`consistency_reconcile.py:24-32,88-94,175-247`).

With `GRAPHITI_ENABLED` on, the same run also looks for graph writes that were
lost (#2341). Postgres records an extraction as finished while the graph
persists by snapshot only, so a graph database that is killed during its
shutdown save can lose episodes that Postgres already lists. For each
organisation `lost_graph_extractions` (`consistency_reconcile.py:136`) takes
the graph rows (`pg_store.get_graph_rows_for_org`, which now also returns
`created_at`, `pg_store.py:1202`), keeps the active rows whose extraction is
finished, records at least one episode id and is older than
`graph.ORPHAN_GRACE` of 24 hours (`graph.py:715`), and counts those whose
recorded ids are not all among the episode uuids the graph holds
(`graph.stored_episode_uuids`, `graph.py:718`). The event gains `graph_check`
(`ok`, `error` or `disabled`), `graph_lost_extractions`, `graph_lost_by_org`
and a sample of at most 20 organisation and artifact ids, ids only. A loss
makes the status `failed`; a graph that cannot be read sets `graph_check` to
`error` and the status to `error`, and is never counted as missing episodes
(`consistency_reconcile.py:183-213`). Both reach the existing
`pg_qdrant_reconcile_failed` alert (`deploy/grafana/provisioning/alerting/ingest-rules.yaml:118`), where
`discrepancies_total` is the Qdrant side and `graph_lost_extractions` the graph
side, and one loss that nobody repairs keeps the alert firing every night
(the comment at `:114-117`). The check detects and does not repair; the
repair of one document is the `reextract_document_graph` script of 5.2.
[pg-qdrant-reconcile.md](../runbooks/pg-qdrant-reconcile.md) has the alert
procedure. To make a lost write less likely, the `falkordb` service has a stop
grace period of 60 s instead of Docker's 10 s, because the shutdown save of the
1.3 GB dump took about 7 s (`deploy/docker-compose.yml:2146-2155`). When
measured on 12 November 2026, 290 of 10,922 recorded episode ids in one
organisation were missing from the graph; #2341 calls the link to restarts
likely and not proven.

Taxonomy jobs: `run_taxonomy_backfill` (four phases: content labels, migrate the
node id field, classify the unclassified, tags; `taxonomy_tasks.py:168-215`),
the hourly sweep (2.7), `run_auto_categorise`, and the bootstrap, classify,
remove-node, replace-node, top-tags and coverage routes (`routes/taxonomy.py:301-1087`).

### 7.3 Crawl health

Alerts `crawl_crawler_errors`, `crawl_fleet_signature`,
`crawl_success_below_baseline` and `crawl_jobs_silent`
(`deploy/grafana/provisioning/alerting/crawl-rules.yaml:51,139,223,327`) and the
session alert in `deploy/grafana/provisioning/alerting/crawl-session-rules.yaml:36`; triage in
[crawl-failures.md](../runbooks/crawl-failures.md).

## Part 8: Key files

| Area | Files |
|---|---|
| App and workers | `app.py`, `worker.py`, `queues.py`, `zombie_recovery.py`, `stale_pending_artifact_reaper.py` |
| Synchronous ingest | `routes/ingest.py`, `chunker.py`, `content_profiles.py`, `embedder.py`, `sparse_embedder.py`, `qdrant_store.py`, `pg_store.py`, `cheap_layer.py`, `kb_config.py` |
| Enrichment | `enrichment_tasks.py`, `enrichment.py`, `enrichment_policy.py`, `contextual.py`, `llm_throttle.py`, `llm_capacity.py` |
| Graph | `graph.py`, `episode_text.py`, `graph_refresh.py` |
| Crawl | `routes/crawl.py`, `routes/crawl_sync.py`, `crawl_tasks.py`, `adapters/crawler.py`, `crawl4ai_client.py`, `crawl4ai_config.py`, `crawl_checkpoint.py` |
| Delete and lifecycle | `document_purge.py`, `connector_cleanup.py`, `connector_purge_tasks.py`, `connector_state.py`, `resource_jobs.py` |
| Operations | `rebuild_tasks.py`, `consistency_reconcile.py`, `scripts/` |
| Connector | `klai-connector/app/services/sync_engine.py`, `scheduler.py`, `parser.py`, `klai-connector/app/adapters/` |
| Upload | `klai-portal/backend/app/api/app_knowledge_sources.py`, `services/kb_upload_poller.py`, `services/docling_client.py`, `services/archive.py` |
| Shared libraries | `klai-libs/image-storage`, `klai-libs/connector-credentials` |
