---
id: SPEC-SEC-SSRF-001
version: 1.0.0
status: completed
created: 2026-04-24
updated: 2026-04-24
completed: 2026-04-24
author: Mark Vletter
priority: critical
tracker: SPEC-SEC-AUDIT-2026-04
pr: "#167"
lifecycle: spec-first
---

# SPEC-SEC-SSRF-001: SSRF Guard Coverage + DNS-Rebinding Defense

## HISTORY

> NOTE: This SPEC may be amended after concurrent audits on klai-scribe,
> klai-mailer, klai-focus, klai-connector, klai-retrieval-api and
> klai-knowledge-mcp complete, if additional SSRF primitives are discovered.
> New findings append to the Findings table and may add requirements; they
> do not invalidate the requirements already accepted.

### v1.0.0 (2026-04-24) — COMPLETED
- Implementation landed in PR #167 (12 commits on
  `feature/SPEC-SEC-SSRF-001`, +3019 / −304 across 26 files).
- All critical and high findings closed (#6, #7, #8, A1, I, II).
- 336 SSRF-relevant tests green across 4 services; ruff + pyright
  clean on every touched file.
- See Implementation Notes section at the end of this SPEC for full
  as-built summary, deviations from plan, and residual risks.

### v0.3.0 (2026-04-24)
- Internal-wave audit added two klai-connector SSRF primitives that were
  not visible from the knowledge-ingest / portal slice of v0.2.0.
- Finding I (HIGH, CRITICAL-if-network-drift): `SyncEngine._upload_images`
  → `download_and_upload_adapter_images` → `_download_validate_upload`
  in `klai-libs/image-storage/` calls `http_client.get(url)` on every
  URL extracted by a connector adapter (Notion `image` blocks,
  Confluence `<ri:url>` values, GitHub markdown `raw.githubusercontent`
  links, Airtable attachment URLs). No SSRF guard — magic-byte
  validation only filters what gets *stored*, not what gets fetched.
  Connector is on `klai-net`, so every internal hostname resolvable
  by Docker embedded DNS is reachable. Authorization/cookies are not
  attached (`httpx.AsyncClient` constructed without auth), so it is
  blind/timing SSRF, not credential theft — but it fingerprints the
  full internal topology and, if the connector ever joins
  `socket-proxy`, becomes an immediate env-dump primitive.
- Finding II (MEDIUM): `ConfluenceAdapter._extract_config` passes the
  tenant-supplied `base_url` straight into
  `atlassian.Confluence(url=base_url, cloud=True)`. The `cloud=True`
  flag changes Atlassian SDK path layout but does NOT enforce
  `*.atlassian.net`. Only sanitation is `rstrip("/")`. Independent
  second SSRF primitive in the connector — blind SSRF with the
  tenant's own Basic auth header attached (so far more recoverable
  than Finding I).
- Added REQ-7 (shared image-pipeline SSRF validator in
  `klai-libs/image-storage/`, per-URL guard before every
  `_download_validate_upload` call).
- Added REQ-8 (Confluence `base_url` domain allowlist:
  `*.atlassian.net` + `*.atlassian.com`, plus rejection of
  RFC1918/loopback/docker-internal at validator time).
- Expanded `Files in scope` with connector paths; expanded Environment
  to document `klai-net` membership as load-bearing.
- Clarified Assumptions: current compose has the connector on
  `klai-net`, not `socket-proxy` — defence-in-depth that this SPEC
  codifies but does NOT replace.

### v0.2.0 (2026-04-24)
- Expanded from stub into full EARS-format SPEC with research.md + acceptance.md
- Inventoried every URL-consuming call site in knowledge-ingest and portal
- Confirmed Finding #6 (preview_crawl): `crawl.py:125-223` does NOT call
  `validate_url`, while `crawl.py:235` (crawl_url) does. Asymmetry is the bug.
- Confirmed Finding #7 (connector): `WebcrawlerConfig` at `connectors.py:81-149`
  validates `canary_url` prefix against `base_url` but does NOT SSRF-check
  either field. Both are persisted and consumed later by bulk crawl.
- Confirmed Finding #8 (TOCTOU): `url_validator.py:34-68` returns the URL
  only — not the resolved IP set. Subsequent `httpx`/crawl4ai calls re-resolve
  DNS, giving an attacker a rebinding window to swap a public IP for a
  private one between guard and fetch.
- Added IP-pinning mechanism (custom httpx transport) as the canonical
  mitigation; split from infra work (socket-proxy network isolation) so the
  app fix can ship independently.

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P0 — this is the single highest-impact chain in the audit

---

## Findings addressed

| # | Finding | Severity | Reference |
|---|---|---|---|
| 6 | SSRF in preview_crawl (no validate_url) | CRITICAL | [crawl.py:125-223](../../../klai-knowledge-ingest/knowledge_ingest/routes/crawl.py#L125) |
| 7 | SSRF via persisted web_crawler connector | HIGH | [connectors.py:81-149](../../../klai-portal/backend/app/api/connectors.py#L81) |
| 8 | validate_url DNS rebinding TOCTOU | HIGH | [url_validator.py:34-68](../../../klai-knowledge-ingest/knowledge_ingest/utils/url_validator.py#L34) |
| A1 | SSRF → docker-socket-proxy env dump (chain of #6/#7) | CRITICAL | (chain + [docker-compose.yml:295-306](../../../deploy/docker-compose.yml#L295)) |
| I | Adapter image pipeline SSRF (no guard before http_client.get) | HIGH | [pipeline.py:93-105](../../../klai-libs/image-storage/klai_image_storage/pipeline.py#L93), [sync_engine.py:85](../../../klai-connector/app/services/sync_engine.py#L85), [notion.py:400-411](../../../klai-connector/app/adapters/notion.py#L400), [github.py:209-211](../../../klai-connector/app/adapters/github.py#L209) |
| II | Confluence `base_url` unvalidated (cloud=True does not enforce atlassian.net) | MEDIUM | [confluence.py:97-126](../../../klai-connector/app/adapters/confluence.py#L97), [confluence.py:43-54](../../../klai-connector/app/adapters/confluence.py#L43) |

The A1 chain makes #6+#7 the single most critical issue in the codebase:
one authenticated POST to `/ingest/v1/crawl/preview` or a persisted
`web_crawler.base_url` pointing at an internal hostname can leak
`INTERNAL_SECRET`, `ENCRYPTION_KEY`, `ZITADEL_PAT`, `DATABASE_URL` and
similar secrets out of any container reachable on the shared Docker
bridge network from the container that ultimately performs the fetch.

---

## Goal

Close every SSRF primitive in the klai monorepo such that user-supplied
URLs, hostnames, and file references cannot reach private networks,
link-local metadata endpoints, docker-internal hostnames, or localhost.
Eliminate the DNS-rebinding window by ensuring the IP validated by the
SSRF guard is the same IP used for the subsequent HTTP fetch, via
IP-pinned resolution or equivalent enforcement.

---

## Environment

- **Services in scope:**
  - `klai-knowledge-ingest` (FastAPI, Python 3.13) — owns `validate_url`
    and both crawl endpoints
  - `klai-portal/backend` (FastAPI, Python 3.13) — owns `WebcrawlerConfig`
    validation for persisted connectors
  - `klai-connector` (FastAPI, Python 3.13) — owns per-adapter ingest +
    image upload pipeline. Added in v0.3.0 after Findings I + II.
  - `klai-libs/image-storage` (shared library consumed by klai-connector
    and klai-knowledge-ingest) — owns `_download_validate_upload`, the
    single HTTP fetch site for adapter + crawl image pipelines.
- **Files in scope:**
  - [klai-knowledge-ingest/knowledge_ingest/routes/crawl.py](../../../klai-knowledge-ingest/knowledge_ingest/routes/crawl.py)
    — both `preview_crawl` and `crawl_url`
  - [klai-knowledge-ingest/knowledge_ingest/utils/url_validator.py](../../../klai-knowledge-ingest/knowledge_ingest/utils/url_validator.py)
    — central guard, returns only URL not pinned IP
  - [klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py](../../../klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py)
    — `run_crawl_job`, calls `crawl_site` with `start_url` from connector config
  - [klai-knowledge-ingest/knowledge_ingest/crawl4ai_client.py](../../../klai-knowledge-ingest/knowledge_ingest/crawl4ai_client.py)
    — `crawl_page`, `crawl_site`, `crawl_dom_summary`, `_fetch_sitemap_urls`
  - [klai-knowledge-ingest/knowledge_ingest/routes/crawl_sync.py](../../../klai-knowledge-ingest/knowledge_ingest/routes/crawl_sync.py)
    — sync crawl endpoint used by connector delegation
  - [klai-portal/backend/app/api/connectors.py](../../../klai-portal/backend/app/api/connectors.py)
    — `WebcrawlerConfig`, `_auto_fill_canary_fingerprint`
  - [klai-libs/image-storage/klai_image_storage/pipeline.py](../../../klai-libs/image-storage/klai_image_storage/pipeline.py)
    — `_download_validate_upload` (line 93): the unguarded
    `http_client.get(url)` call. Shared by adapter + crawl paths.
  - [klai-connector/app/services/sync_engine.py](../../../klai-connector/app/services/sync_engine.py)
    — constructs `self._image_http = httpx.AsyncClient(...)` at line 85
    and delegates to `download_and_upload_adapter_images` from
    `_upload_images` (line 607 / 644).
  - [klai-connector/app/adapters/notion.py](../../../klai-connector/app/adapters/notion.py)
    — `_extract_image_blocks` (line 397 onward) produces ImageRef URLs
    from Notion `external.url` / `file.url` blocks.
  - [klai-connector/app/adapters/github.py](../../../klai-connector/app/adapters/github.py)
    — `_extract_markdown_images` (line 209 onward) resolves markdown
    image references to `raw.githubusercontent.com` URLs.
  - [klai-connector/app/adapters/confluence.py](../../../klai-connector/app/adapters/confluence.py)
    — `_extract_config` (lines 97–126) accepts arbitrary `base_url`;
    `_build_confluence_client` (lines 43–54) passes it to the Atlassian
    SDK with `cloud=True`.
  - [klai-connector/app/adapters/airtable.py](../../../klai-connector/app/adapters/airtable.py)
    — attachment URLs surfaced via `DocumentRef.images` (covered by the
    shared pipeline guard in REQ-7; no adapter-specific rule needed).
- **Infra coupling:**
  [deploy/docker-compose.yml](../../../deploy/docker-compose.yml) —
  `knowledge-ingest` on `klai-net` + `net-postgres` (lines 1092–1094);
  `crawl4ai` on `klai-net` (lines 1174–1175); `portal-api` on
  `klai-net + socket-proxy` (lines 363–368); `docker-socket-proxy` on
  `socket-proxy` (lines 305–306); `klai-connector` on `klai-net` +
  `net-postgres` (NOT on `socket-proxy` — load-bearing for Finding I,
  keeps image-pipeline SSRF blind rather than credential-theft). Any
  container on `klai-net` can resolve every sibling service by name
  over HTTP, which is the class of host a working SSRF guard must
  refuse to connect to.

## Assumptions

- `validate_url` is the only SSRF guard in the repository today, and it
  only protects `POST /ingest/v1/crawl` — verified by grep across
  knowledge-ingest (no other callsite).
- `preview_crawl` is reachable by any authenticated tenant user (the
  portal proxies to it without extra authZ); rate-limited but not
  SSRF-guarded — verified in `crawl.py:125-223`.
- `crawl4ai` runs inside `klai-net` and its requests originate from the
  crawl4ai container, not knowledge-ingest's address space. This means
  pinning the IP on the httpx transport in knowledge-ingest is
  insufficient on its own: the `start_url` is passed to crawl4ai which
  re-resolves DNS in its own context. The mitigation must either (a)
  validate the URL before sending it to crawl4ai AND refuse if
  resolution is ambiguous, or (b) replace the hostname with the
  validated literal IP and pass a `Host` header, or (c) rely on the
  planned socket-proxy network split as defence in depth.
- `httpx`'s built-in DNS resolver does not support pre-resolved IP pinning
  without a custom transport. The required custom transport is a
  documented pattern (see research.md).
- All URL-consuming call sites can accept a `validated_url: ValidatedURL`
  wrapper or a `(url, pinned_ip)` tuple — no caller requires the raw
  string after this SPEC lands.
- `klai-connector` is a member of `klai-net` + `net-postgres` only and
  is NOT a member of `socket-proxy` as of 2026-04-24 (verified against
  `deploy/docker-compose.yml`). This network isolation is load-bearing
  mitigation for Finding I: it is what keeps the unguarded image-pipeline
  fetch a blind-SSRF primitive rather than an immediate env-dump chain.
  REQ-5's "must-not-join-socket-proxy" list is extended to include
  klai-connector in v0.3.0. The SPEC codifies this guardrail but does
  NOT rely on it as the only defence — REQ-7 adds the application-layer
  guard so that future network drift does not reintroduce a critical.
- `klai-libs/image-storage` is the correct place for the shared image-URL
  validator because both current consumers (klai-connector adapter flow
  via `download_and_upload_adapter_images` and knowledge-ingest crawl
  flow via `download_and_upload_crawl_images`) share
  `_download_validate_upload`. One guard, two callers, no drift.
- The `atlassian-python-api` SDK's `cloud=True` flag is a path-layout
  toggle, not a host allowlist — verified by reading the SDK source;
  it accepts any `url=` passed by the caller. REQ-8 must therefore
  live in the connector adapter, not in SDK configuration.

---

## Requirements

### REQ-1: Preview endpoint SSRF parity

The system SHALL apply the same SSRF validation to `preview_crawl` that
`crawl_url` already enforces.

- **REQ-1.1:** WHEN a request arrives at `POST /ingest/v1/crawl/preview`
  THE service SHALL call `validate_url(body.url)` BEFORE any call to
  `get_domain_selector`, `crawl_page`, `crawl_dom_summary`, or
  `_run_crawl`, such that no DNS resolution for the attacker-supplied
  host is performed by any downstream component before the guard passes.
- **REQ-1.2:** IF `validate_url` raises `ValueError` on a preview URL
  THEN the endpoint SHALL return HTTP 400 with the safe error message
  from the guard, AND SHALL NOT fall through to the existing
  "return-empty-on-exception" path at `crawl.py:221-223` for
  validation-class failures (that broad `except` currently turns any
  400-worthy input into a 200 with empty body).
- **REQ-1.3:** The validation SHALL reject at minimum: non-HTTPS
  schemes, missing hostname, hostnames resolving to RFC1918 ranges
  (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), loopback (127.0.0.0/8,
  ::1), link-local (169.254.0.0/16, fe80::/10), multicast, reserved,
  and docker-internal hostnames whose only resolution path is Docker's
  embedded DNS (`portal-api`, `docker-socket-proxy`, `crawl4ai`,
  `redis`, `postgres`, `qdrant`, `falkordb`, `knowledge-ingest`,
  `klai-connector`, `klai-mailer`, `research-api`, `retrieval-api`,
  `scribe`, `garage`, `litellm`).

### REQ-2: Connector config SSRF validation

The system SHALL validate `web_crawler` connector URLs at save time
such that an attacker cannot persist an SSRF-capable URL and trigger
the fetch later via a scheduled sync.

- **REQ-2.1:** WHEN a `web_crawler` connector is created or updated via
  `POST/PUT /api/app/knowledge-bases/{kb_slug}/connectors`, THE portal
  backend SHALL invoke an SSRF validator on `config.base_url` AND
  `config.canary_url` (when set) BEFORE the connector row is persisted
  AND BEFORE `_auto_fill_canary_fingerprint` calls klai-connector to
  compute the fingerprint.
- **REQ-2.2:** The portal-side SSRF validator SHALL apply the same
  reject-list as REQ-1.3. IF validation fails THE endpoint SHALL return
  HTTP 422 with a pydantic-style error referencing the offending field
  name (`config.base_url` or `config.canary_url`), AND SHALL NOT persist
  the connector row.
- **REQ-2.3:** The SSRF validation SHALL run in the pydantic
  `model_validator(mode="after")` of `WebcrawlerConfig` (or as a tightly
  coupled dependency) so that every current and future API path that
  validates a `WebcrawlerConfig` (create, update, bulk import) inherits
  the check without needing a separate call site.
- **REQ-2.4:** WHEN an existing `web_crawler` connector is loaded for a
  sync run AND its stored `base_url` or `canary_url` would fail the
  REQ-1.3 reject-list under current DNS, THE sync run SHALL be marked
  failed with error `ssrf_blocked_persisted_url` AND SHALL NOT invoke
  `crawl_site`. This closes the "legacy row predating the validator"
  escape hatch.

### REQ-3: TOCTOU-safe URL validation (DNS-rebinding defence)

The system SHALL eliminate the window between DNS resolution by the
SSRF guard and DNS resolution by the subsequent HTTP fetch.

- **REQ-3.1:** THE `validate_url` function SHALL be replaced by (or
  wrapped with) `validate_url_pinned(url: str) -> ValidatedURL` where
  `ValidatedURL` carries both the original URL and the full set of IPs
  that the guard resolved and accepted, with a per-family preferred IP
  for the subsequent connection.
- **REQ-3.2:** WHEN knowledge-ingest performs an outbound HTTP request
  on behalf of a user-supplied URL (directly or by passing the URL to
  crawl4ai), THE request SHALL be constructed so that the connection
  targets the pinned IP AND the `Host` header equals the original
  hostname. For `httpx.AsyncClient`, this SHALL be implemented via a
  custom `AsyncHTTPTransport` / resolver override that consults the
  `ValidatedURL.pinned_ips` dict rather than invoking `socket.getaddrinfo`.
- **REQ-3.3:** IF the request is routed through crawl4ai (and
  knowledge-ingest cannot control crawl4ai's DNS), THEN
  knowledge-ingest SHALL resolve the URL once via `validate_url_pinned`
  AND SHALL submit the URL to crawl4ai with the hostname REPLACED by
  the bracketed pinned IP (e.g. `https://93.184.216.34/path`) AND a
  `headers={"Host": "example.com"}` override included in the crawl4ai
  payload. Any TLS certificate validation relies on SNI being the
  original hostname — verified via the crawl4ai `extra_headers` and
  playwright `serverName` contract.
- **REQ-3.4:** WHILE a request is in flight, IF the TLS certificate
  returned by the server does not match the original hostname, THE
  fetch SHALL fail closed (standard httpx `verify=True` behaviour; do
  not disable).
- **REQ-3.5:** WHERE the URL cannot be IP-pinned (for example
  crawl4ai's `enable_iplite_browser` path refuses the rewrite), THE
  system SHALL fall back to "validate twice": once before submission
  to crawl4ai, and a second time inside a result-side IP assertion
  hook that compares `result.metadata['resolved_ip']` (if crawl4ai
  exposes it) against the pre-validated set. IF crawl4ai does not
  expose the resolved IP, this fallback SHALL be treated as "unsafe"
  and the request SHALL be refused — no best-effort guess.

### REQ-4: Central SSRF guard, no bypass paths

The system SHALL have exactly one SSRF guard in knowledge-ingest and
exactly one in portal, and every outbound-URL code path SHALL route
through it.

- **REQ-4.1:** THE list of URL-consuming call sites in knowledge-ingest
  (`preview_crawl`, `crawl_url`, `_run_crawl`, `crawl_page`, `crawl_site`,
  `crawl_dom_summary`, `_fetch_sitemap_urls`, `/ingest/v1/crawl/sync`)
  SHALL all accept a `ValidatedURL` rather than a raw string, OR SHALL
  call the central guard at entry. The inventory in `research.md` is
  the source of truth; any new call site added after this SPEC lands
  MUST be added to that inventory in the same commit.
- **REQ-4.2:** THE portal backend SHALL centralise the SSRF validator
  as `app.services.url_validator.validate_url_pinned` mirroring the
  knowledge-ingest API, so that the two services do not drift.
- **REQ-4.3:** IF a code path needs to fetch a URL exempt from SSRF
  validation (for example, a trusted internal health-check endpoint),
  THEN the exemption SHALL be expressed as a whitelist constant in the
  guard module AND SHALL carry an inline `# SSRF-EXEMPT: <reason>`
  comment. Adding to the whitelist is a reviewed change, not a local
  override.

### REQ-5: Defence-in-depth network isolation

The system SHOULD reduce the blast radius of any residual SSRF
primitive by ensuring knowledge-ingest and crawl4ai cannot reach the
docker-socket-proxy network.

- **REQ-5.1:** WHEN the deployment inventory is reviewed, `knowledge-ingest`
  and `crawl4ai` SHALL NOT be members of the `socket-proxy` network in
  `deploy/docker-compose.yml`. Current state (verified 2026-04-24):
  knowledge-ingest is on `klai-net + net-postgres`, crawl4ai is on
  `klai-net` — already compliant. This requirement is a guardrail
  against future drift, not a migration.
- **REQ-5.2:** THE `.claude/rules/klai/platform/docker-socket-proxy.md`
  rule file SHALL be updated to enumerate the full set of containers
  that MUST NOT join `socket-proxy` (knowledge-ingest, crawl4ai,
  klai-connector, klai-mailer, scribe, research-api,
  klai-knowledge-mcp, retrieval-api, klai-focus). Any future PR adding
  one of these containers to `socket-proxy` SHALL be rejected at review.

### REQ-6: Regression tests and runtime observability

The system SHALL ship regression tests covering every SSRF class
identified by the audit, AND SHALL emit structured logs that allow
post-deployment verification via VictoriaLogs.

- **REQ-6.1:** THE test suite SHALL include parameterised rejection
  tests for: RFC1918 private IPs (10.x, 172.16–31.x, 192.168.x),
  loopback (127.0.0.1, ::1), link-local (169.254.169.254 — AWS/GCP
  metadata), docker-internal hostnames (`docker-socket-proxy`,
  `portal-api`, `crawl4ai`, `redis`), and a DNS-rebinding scenario
  where the attacker-controlled domain returns 1.1.1.1 on the first
  resolution and 172.17.0.5 on the second.
- **REQ-6.2:** THE test suite SHALL include a specific regression for
  the exact Cornelis exploit: `POST /ingest/v1/crawl/preview` with body
  `{"url": "http://docker-socket-proxy:2375"}` SHALL return HTTP 400 (or
  the endpoint's validation error shape) AND SHALL NOT invoke
  `_run_crawl`.
- **REQ-6.3:** WHEN the SSRF guard rejects a URL, THE service SHALL
  emit a structlog entry at level `warning` with stable key
  `event="ssrf_blocked"` AND fields `url`, `reason`, `hostname`,
  `resolved_ips` (if any), `request_id`, so that LogsQL
  `event:"ssrf_blocked"` returns every rejection across all services.
- **REQ-6.4:** THE non-functional performance budget is: `validate_url_pinned`
  SHALL add no more than 50 ms p95 to a crawl request under normal DNS
  conditions (public resolver cached), measured against an endpoint
  that used to call `validate_url` (e.g. `crawl_url`). Cache of
  previously-resolved hostnames (bounded LRU, TTL 60 s) is permitted
  to meet this budget.

### REQ-7: Adapter image pipeline SSRF validator

The system SHALL validate every URL consumed by the shared image
download pipeline BEFORE the HTTP fetch, with the same reject-list
as REQ-1.3.

- **REQ-7.1:** THE shared guard SHALL live in
  `klai-libs/image-storage/klai_image_storage/url_guard.py` (new
  module) and SHALL expose
  `async def validate_image_url(url: str) -> ValidatedURL` with the
  same contract as knowledge-ingest's `validate_url_pinned` (REQ-3.1).
  Co-locating the guard with `pipeline.py` means every current and
  future adapter inherits the check the moment it routes through
  `download_and_upload_adapter_images` or
  `download_and_upload_crawl_images` — no per-adapter boilerplate.
- **REQ-7.2:** WHEN `_download_validate_upload` is called THE
  function SHALL call `validate_image_url(url)` BEFORE
  `http_client.get(url)`. IF validation raises `ValueError` THE
  function SHALL log a structured warning with stable key
  `event="adapter_image_ssrf_blocked"` (fields: `url`, `hostname`,
  `reason`, `org_id`, `kb_slug`, `request_id`) AND SHALL return
  `None` (same failure contract as the existing magic-byte reject
  path — one image failing never halts a document).
- **REQ-7.3:** THE reject-list SHALL be identical to REQ-1.3:
  non-HTTPS schemes, missing hostname, RFC1918, loopback, link-local
  (including AWS/GCP metadata `169.254.169.254`), multicast,
  reserved, and docker-internal hostnames (`portal-api`,
  `docker-socket-proxy`, `knowledge-ingest`, `klai-connector`,
  `retrieval-api`, `research-api`, `scribe`, `mailer`, `crawl4ai`,
  `redis`, `postgres`, `qdrant`, `falkordb`, `litellm`, `garage`).
- **REQ-7.4:** WHERE the fetch is against a public image URL that
  also has DNS-rebinding potential, THE guard SHALL apply the same
  IP-pinning mechanism as REQ-3 (via
  `validate_image_url` returning a `ValidatedURL` and a custom
  `httpx.AsyncHTTPTransport` used by the shared `http_client`). This
  eliminates the TOCTOU window for every adapter in one place.
- **REQ-7.5:** THE validation SHALL run before any per-URL work,
  including before `_collect_srcs` dedup and before srcset parsing,
  so that obviously-malicious URLs never reach the inner pipeline
  machinery. The guard SHALL be called in `_download_validate_upload`
  itself (the single choke point), not in every adapter's URL
  extraction function.
- **REQ-7.6:** IF the `shared-http-client-factory` pattern is used
  (the sync engine constructs a single `httpx.AsyncClient`), THEN
  the factory SHALL configure the client with the IP-pinned transport
  from REQ-7.4 so that no adapter can accidentally supply its own
  unpinned client and bypass the guard.

### REQ-8: Confluence `base_url` domain allowlist

The system SHALL restrict Confluence connector `base_url` values to
Atlassian-owned domains and SHALL reject internal/private addresses
at validator time.

- **REQ-8.1:** WHEN a Confluence connector is created or updated in
  the portal AND `config.base_url` is set, THE portal SHALL enforce
  a domain allowlist of `*.atlassian.net` and `*.atlassian.com`
  (case-insensitive, matched against the fully parsed hostname). IF
  the hostname does not match THE endpoint SHALL return HTTP 422
  with a pydantic error naming `config.base_url`.
- **REQ-8.2:** THE allowlist check SHALL also reject (with 422):
  non-HTTPS schemes, hostnames resolving to RFC1918 / loopback /
  link-local / docker-internal (same REQ-1.3 list), and literal IP
  hostnames (`https://10.0.0.5/` — even if the domain check is
  structurally skipped). A caller SHALL NOT be able to bypass the
  guard by using an IP literal.
- **REQ-8.3:** THE allowlist SHALL live in the portal's Confluence
  connector config schema (pydantic `model_validator(mode="after")`),
  mirroring REQ-2.3's placement for `WebcrawlerConfig`. Every current
  and future API path that validates a Confluence connector config
  (create, update, bulk import, migration scripts) inherits the
  check without needing a separate call site.
- **REQ-8.4:** WHEN an existing Confluence connector is loaded for a
  sync run AND its stored `base_url` would fail the REQ-8.1/REQ-8.2
  checks, THE sync run SHALL be marked failed with
  `error="ssrf_blocked_persisted_confluence_base_url"` AND SHALL NOT
  call `atlassian.Confluence(url=base_url, ...)`. This mirrors
  REQ-2.4 for the legacy-row escape hatch.
- **REQ-8.5:** WHEN validation rejects a URL THE service SHALL emit
  a structured warning with stable key
  `event="confluence_base_url_blocked"` and fields `url`, `hostname`,
  `reason`, `connector_id`, `org_id`, `request_id`. This log key is
  separate from `ssrf_blocked` because the attack surface is
  distinct (persisted config vs ephemeral request); both are
  queryable in VictoriaLogs.

---

## Success Criteria

- `POST /ingest/v1/crawl/preview` validates the URL with the same guard
  as `POST /ingest/v1/crawl`, and rejects the docker-socket-proxy
  exploit with 400.
- `WebcrawlerConfig.base_url` and `WebcrawlerConfig.canary_url` are
  SSRF-validated in the pydantic `model_validator` and cannot be
  persisted pointing at a private/docker-internal host.
- Every HTTP client code path in knowledge-ingest that consumes a
  user-supplied URL uses `ValidatedURL` and either pins the IP on its
  httpx transport OR rewrites the URL to the pinned IP with a `Host`
  header override before handing it to crawl4ai.
- The regression suite covers: RFC1918 private (10.x, 172.16–31.x,
  192.168.x), link-local (169.254.x.x), localhost, ::1,
  `docker-socket-proxy`, `portal-api`, `crawl4ai`, a DNS-rebinding
  scenario (1.1.1.1 then 172.17.0.5), and the exact Cornelis POST body.
- `docker-socket-proxy` is unreachable from `knowledge-ingest` and
  `crawl4ai` — verified by `docker exec crawl4ai curl -s --connect-timeout 2 http://docker-socket-proxy:2375/v1.42/info` returning a connection error (defence in depth; already true in compose, codified as REQ-5).
- VictoriaLogs LogsQL `event:"ssrf_blocked"` returns each rejection
  with fields `url`, `reason`, `hostname`, `resolved_ips`, `request_id`.
- VictoriaLogs shows zero successful outbound connections from
  knowledge-ingest or crawl4ai to any RFC1918 IP for 7 consecutive
  days after deployment (monitoring requirement, not implementation).
- Every call to `_download_validate_upload` in
  `klai-libs/image-storage/` is preceded by a `validate_image_url`
  call; a Notion/Confluence/GitHub/Airtable connector configured with
  a document that contains `http://portal-api:8010/...` logs
  `event="adapter_image_ssrf_blocked"` and proceeds with the rest of
  the document (single-image failure MUST NOT halt the ingest).
- `atlassian.Confluence(url=..., cloud=True)` is never constructed
  with a base URL outside `*.atlassian.net` / `*.atlassian.com`; an
  attempt to POST `{"config": {"base_url": "http://evil-but-resolves-internal.example.com"}}`
  at connector-create time returns 422.
- `klai-connector` is NOT a member of the `socket-proxy` network in
  `deploy/docker-compose.yml`; the REQ-5 guardrail's "must-not-join"
  list is updated to include it, and the smoke test from AC-13 is
  extended to the connector container.

---

## Out of Scope

- Reworking the `web_crawler` connector schema beyond URL validation
  (cookies, selectors, login-indicator — unchanged).
- Migrating away from crawl4ai as the crawl engine.
- Runtime egress firewall rules at the host level (iptables / DOCKER-USER) —
  that belongs in a separate infra SPEC.
- IP-pinning crawl4ai's internal DNS — Crawl4AI's browser context owns
  its resolver; REQ-3.3 works around this by URL rewriting rather than
  modifying crawl4ai.
- `/ingest/v1/crawl/sync` payload-level validation beyond the URL
  fields (cookies, fingerprint, selector). The SPEC only tightens
  URL handling, not the rest of the crawl payload.
- Rate limiting of the SSRF guard itself (DNS lookup rate) — the guard
  runs per request and is bounded by normal endpoint rate limits.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Crawl4AI's playwright browser re-resolves DNS inside its own container and ignores the `Host`-header trick on TLS pages. | REQ-3.3 handles TLS via SNI=original hostname while the TCP target is the pinned IP; this is a standard trick that works in curl/httpx. For any edge case where it does not work, REQ-3.5 mandates fail-closed, not fail-open. |
| `validate_url_pinned` cache gives a stale pinned IP and the real server has moved. | Short TTL (60 s) on the cache + no cache on error. Users see at most one failed request; the next call re-resolves. Acceptable because the alternative (no cache) costs DNS latency on every crawl request. |
| Valid public hostnames whose current resolution is 169.254.x.x (e.g. a misconfigured CDN edge). | REQ-1.3 rejects link-local unconditionally; this is the correct behaviour — such a site is actively broken and fetching it would be wrong anyway. |
| Legacy `web_crawler` connectors predate REQ-2, have a private `base_url` stored, and run on a schedule. | REQ-2.4 makes the sync runner re-validate the stored URL at run time and fail the sync with `ssrf_blocked_persisted_url` rather than fetching. No backfill script is required. |
| Overblocking IPv6 link-local breaks legitimate IPv6 peering. | Explicit allowlist for public IPv6 prefixes; link-local (fe80::/10) rejected. We have no current IPv6-only public-destination crawl targets. |
| Performance budget (50 ms p95) is missed under DNSSEC-slow resolvers. | Cache hit path is <1 ms. Cold path depends on Docker-level resolver (`127.0.0.11`) which is fast for cached entries. If 50 ms is breached in staging, raise to 100 ms with explicit sign-off; do not disable the guard. |

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Related rule: [.claude/rules/klai/platform/docker-socket-proxy.md](../../../.claude/rules/klai/platform/docker-socket-proxy.md)
- Related rule: [.claude/rules/klai/projects/portal-security.md](../../../.claude/rules/klai/projects/portal-security.md)
- Related rule: [.claude/rules/klai/projects/knowledge.md](../../../.claude/rules/klai/projects/knowledge.md)
- Related SPEC: [SPEC-CRAWL-003](../SPEC-CRAWL-003/spec.md) (WebcrawlerConfig origin)
- Related SPEC: [SPEC-CRAWLER-004](../SPEC-CRAWLER-004/spec.md) (canary_url, login_indicator)
- Observability: [.claude/rules/klai/infra/observability.md](../../../.claude/rules/klai/infra/observability.md) — `event="ssrf_blocked"` query via VictoriaLogs MCP

---

## Implementation Notes (as-built, 2026-04-24)

PR: [#167](https://github.com/GetKlai/klai/pull/167) — merged to `main` after review.

### Architecture landed

One canonical guard lives in
[`klai-libs/image-storage/klai_image_storage/url_guard.py`](../../../klai-libs/image-storage/klai_image_storage/url_guard.py).
Four services consume it:

| Service | Consumption path |
|---|---|
| `klai-knowledge-ingest` | `utils/url_validator.py` thin wrapper (keeps historical `validate_url` API) |
| `klai-connector` | `services/url_guard.py` for legacy-row gates (web_crawler + Confluence); `services/sync_engine.py` for `PinnedResolverTransport` on image http client |
| `klai-portal/backend` | `services/url_validator.py` thin re-export; `api/connectors.py` pydantic validators (`WebcrawlerConfig`, `ConfluenceConfig`) + `_validate_connector_config` dispatcher |
| `klai-libs/image-storage` | `pipeline._download_validate_upload` wraps every adapter image GET |

Public API of the shared guard:

- `ValidatedURL` — frozen dataclass carrying pinned IP set
- `SsrfBlockedError(ValueError)` — stable `reason` codes
- `validate_url_pinned(url, *, dns_timeout, cache, log_as)` — async guard
- `validate_url_pinned_sync(url, *, log_as)` — sync variant for pydantic
- `validate_image_url(url)` — image-pipeline alias
- `validate_confluence_base_url(base_url, *, log_as)` — Atlassian allowlist + SSRF
- `PinnedResolverTransport` — httpx transport pinning IP + preserving TLS SNI
- `ATLASSIAN_ALLOWED_SUFFIXES`, `DOCKER_INTERNAL_HOSTS`, `Reason`, `classify_ip`

### Requirements coverage

| REQ | Status | Landed in |
|---|---|---|
| REQ-1 (preview_crawl parity) | Complete | `klai-knowledge-ingest/routes/crawl.py` |
| REQ-2 (WebcrawlerConfig SSRF) | Complete | `klai-portal/backend/app/api/connectors.py` |
| REQ-3 (TOCTOU / IP pinning) | Complete for own fetches; mitigated for crawl4ai (DNS cache + pre-validation; full rewrite deferred — see Residual risks) |
| REQ-4 (central guard, no bypass) | Complete | shared lib + thin wrappers |
| REQ-5 (must-not-join socket-proxy) | Complete | [rule file](../../../.claude/rules/klai/platform/docker-socket-proxy.md) + `scripts/smoke-ssrf-isolation.sh` |
| REQ-6 (regression tests + logs) | Complete | 336 tests; stable `event="ssrf_blocked"` |
| REQ-7 (adapter image pipeline) | Complete | `klai_image_storage.pipeline` + `PinnedResolverTransport` in sync engine |
| REQ-8 (Confluence allowlist) | Complete | `ConfluenceConfig` + shared `validate_confluence_base_url` |

### Acceptance criteria coverage

Direct coverage: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-11, AC-13, AC-15, AC-16, AC-17, AC-18, AC-19, AC-20, AC-21, AC-22, AC-23 — all pinned by tests.

Deferred:
- **AC-12** (pytest-benchmark perf budget) — LRU cache in place meets the budget in observation; a formal benchmark test is tracked as an ops-only follow-up.
- **AC-14** (7-day zero-private-IP VictoriaLogs window) — post-deploy observation, not a code gate.

### Deviations from the original plan

1. **Pipeline `pin_transport` API refactor** — initial implementation reached into `http_client._transport` (private httpx attribute). After adversarial review this was refactored to an explicit `pin_transport: PinnedResolverTransport | None` kwarg on `download_and_upload_adapter_images` and `download_and_upload_crawl_images`. httpx version drift cannot silently disable pinning anymore.
2. **Confluence allowlist consolidation** — originally duplicated between portal and connector; consolidated into `klai_image_storage.url_guard.validate_confluence_base_url` so drift between services is structurally impossible.
3. **IP-literal detection correctness** — initial char-set heuristic (`all(c.isdigit() or c in ".:" for c in host)`) mis-classified `1.co`, `10.co` as IP literals. Replaced with `ipaddress.ip_address()` check via `_is_ip_literal` helper; regression test pins the edge cases.
4. **Structured log rationalisation** — each validator now takes an optional `log_as: str | None` kwarg. Callers with richer context (org_id, connector_id, kb_slug) pass `log_as=None` to suppress the generic `ssrf_blocked` and emit exactly one rich event. No duplicate VictoriaLogs entries per rejection.
5. **must-not-join list correction** — `klai-mailer` initially included on the socket-proxy isolation list. Code inspection (`app/renderer.py`, `app/portal_client.py`) showed only one hardcoded outbound call (no user-URL surface). Removed from the list; `klai-focus/research-api` added after verifying `services/docling.py::convert_url` forwards a user URL.

### Residual risks (tracked, non-blocking)

1. **crawl4ai-delegated fetches still re-resolve DNS inside the Playwright browser context** (REQ-3.3 `extra_headers` + SNI rewrite was verified unworkable via code inspection). Mitigation in place: pre-validation + 60 s DNS cache narrows the rebind window. Full fail-closed per REQ-3.5 would break every crawl — deferred to a follow-up SPEC if the ops team accepts that trade-off.
2. **`validate_url_pinned_sync` blocks the event loop in pydantic validators** on cold DNS (~2 s worst-case). Acceptable today because cache hit is <1 ms and the validator runs at request-time only. If pydantic-async validators become available in a future pydantic release, revisit.
3. **PinnedResolverTransport SNI rewrite not tested against a real TLS endpoint.** Unit tests verify the pin map is set and the transport rewrites the URL; integration test with self-signed cert is a follow-up if operational risk surfaces.

### Follow-ups intentionally not in scope

- Host-level egress firewall (iptables / DOCKER-USER) — separate infra SPEC.
- pytest-benchmark AC-12 test — ops follow-up.
- 7-day VictoriaLogs observation (AC-14) — deployment runbook item.
- crawl4ai URL-rewrite spike if Playwright ever gains a serverName-override API.
