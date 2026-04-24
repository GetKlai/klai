# Acceptance Criteria — SPEC-SEC-SSRF-001

EARS-format acceptance tests that MUST pass before SPEC-SEC-SSRF-001 is
considered complete. Each item is verifiable against code (unit +
integration tests), against live behaviour (docker exec + curl), or
against VictoriaLogs via LogsQL.

## AC-1: Preview endpoint rejects unvalidated URLs

- **WHEN** a client posts `{"url": "https://attacker.example.test/"}` to
  `/ingest/v1/crawl/preview` AND the URL resolves to a public IP
  **THE** endpoint **SHALL** call `validate_url_pinned` BEFORE any call
  to `_run_crawl`, `crawl_page`, `crawl_dom_summary`, or
  `get_domain_selector`.
- **WHEN** the posted URL fails SSRF validation
  **THE** endpoint **SHALL** return HTTP 400 with a safe error message
  **AND SHALL NOT** return the historical 200-with-empty-body
  (the broad `except` at `crawl.py:221-223` is no longer the handler
  for validation-class exceptions).

## AC-2: Private IP rejection (RFC1918)

- **WHEN** the validator is called with a URL whose hostname resolves
  to an RFC1918 address in `10.0.0.0/8`, `172.16.0.0/12`, or
  `192.168.0.0/16` **THE** validator **SHALL** raise `ValueError` AND
  the endpoint **SHALL** respond with HTTP 400.
- Verification matrix (all must reject): `10.0.0.1`, `10.255.255.254`,
  `172.16.0.1`, `172.31.255.254`, `192.168.1.1`, `192.168.100.100`.
- Verification negatives (all must accept under normal DNS):
  `1.1.1.1`, `8.8.8.8`, `93.184.216.34` (example.com).

## AC-3: Link-local and metadata endpoint rejection

- **WHEN** the validator is called with a URL whose hostname resolves
  to `169.254.0.0/16` (IPv4 link-local, includes AWS/GCP metadata
  `169.254.169.254`) OR `fe80::/10` (IPv6 link-local) **THE** validator
  **SHALL** raise `ValueError`.
- **WHEN** the validator is called with loopback `127.0.0.1`, `::1`,
  or `localhost` **THE** validator **SHALL** raise `ValueError`.

## AC-4: Docker-internal hostname rejection

- **WHEN** the validator is called with a URL whose host is one of
  `docker-socket-proxy`, `portal-api`, `crawl4ai`, `redis`, `postgres`,
  `qdrant`, `falkordb`, `knowledge-ingest`, `klai-connector`,
  `klai-mailer`, `research-api`, `retrieval-api`, `scribe`, `garage`,
  `litellm` **THE** validator **SHALL** raise `ValueError` EVEN IF the
  Docker embedded resolver returns a public-looking IP.
- Rationale: these names resolve only on Docker bridges; any user-URL
  hitting them is by construction SSRF.

## AC-5: DNS-rebinding TOCTOU closed

- **WHEN** the validator resolves a hostname AND the same hostname
  resolves to a different IP on a subsequent lookup (rebinding scenario
  — first lookup returns `1.1.1.1`, second lookup returns `172.17.0.5`)
  **THE** HTTP fetch performed on behalf of that URL **SHALL** connect
  to the pinned IP from the first lookup (`1.1.1.1`) AND **SHALL NOT**
  connect to `172.17.0.5`.
- Verification: a test that monkey-patches `socket.getaddrinfo` to
  return different IPs on successive calls, then asserts the httpx
  client's actual TCP `getpeername()` equals the first-resolved IP.
- **WHEN** the pinned IP is unreachable OR the TLS hostname does not
  match **THE** fetch **SHALL** fail closed (standard httpx error
  propagation; `verify=True` stays on).

## AC-6: The exact Cornelis regression

- **WHEN** a client posts
  ```json
  {"url": "http://docker-socket-proxy:2375/v1.42/info"}
  ```
  to `/ingest/v1/crawl/preview` with a valid tenant session
  **THE** endpoint **SHALL** return HTTP 400 with an SSRF rejection
  error **AND SHALL NOT** invoke `_run_crawl`, `crawl_page`, or any
  httpx client targeting the docker-socket-proxy host.
- Also covers the scheme variant: posting `http://...` (not HTTPS) is
  rejected by the scheme check; posting `https://docker-socket-proxy:2375/`
  is rejected by the hostname reject-list (AC-4) even though the scheme
  passes.
- **WHEN** the same body is posted to `POST /ingest/v1/crawl` (the
  already-guarded endpoint) **THE** endpoint **SHALL** also return
  HTTP 400 — regression guard for parity.

## AC-7: Connector config SSRF validation (create)

- **WHEN** the portal receives
  `POST /api/app/knowledge-bases/{kb}/connectors` with
  `connector_type="web_crawler"` AND
  `config.base_url="http://docker-socket-proxy:2375/"`
  **THE** portal **SHALL** return HTTP 422 with a pydantic validation
  error referencing `config.base_url` **AND SHALL NOT** persist the
  connector row **AND SHALL NOT** call
  `_auto_fill_canary_fingerprint` nor `klai_connector_client.compute_fingerprint`.
- **WHEN** the posted `config.base_url` is `http://10.0.0.5/`
  **THE** portal **SHALL** also return 422 (private IP class).
- **WHEN** the posted `config.canary_url` fails validation while
  `config.base_url` passes **THE** portal **SHALL** return 422 naming
  `config.canary_url` as the offending field.

## AC-8: Connector config SSRF validation (update)

- **WHEN** `PUT /api/app/knowledge-bases/{kb}/connectors/{id}` is used
  to change `config.base_url` to an SSRF-unsafe value
  **THE** portal **SHALL** reject with 422 **AND SHALL NOT** mutate the
  stored config.
- Regression guard: the existing XOR/canary-prefix/selector validators
  in `WebcrawlerConfig` still run; this AC does not weaken them.

## AC-9: Legacy connector sync refuses SSRF-unsafe stored URLs

- **WHEN** a scheduled sync run loads a pre-existing `web_crawler`
  connector whose `base_url` is today SSRF-unsafe (stored before
  REQ-2 landed) **THE** sync run **SHALL** be marked failed with
  `error="ssrf_blocked_persisted_url"` **AND SHALL NOT** invoke
  `crawl_site`, `_fetch_sitemap_urls`, or `crawl_page`.
- Verification: insert a row with `base_url=http://redis:6379/` into
  `connector.connectors`, trigger its sync, assert the `sync_runs` row
  ends in `status='failed'` with the `ssrf_blocked_persisted_url`
  error AND that no HTTP fetch was issued.

## AC-10: Central guard, no bypass

- **WHEN** the test suite greps knowledge-ingest source code for
  outbound-URL fetch patterns (`httpx.AsyncClient`, `requests.get`,
  `aiohttp`, `crawl_page`, `crawl_site`, `crawl_dom_summary`,
  `_fetch_sitemap_urls`) **THE** resulting inventory **SHALL** match
  the list in `research.md` §2 exactly. A new call site added after
  this SPEC lands WITHOUT updating `research.md` MUST fail CI (a grep
  check in the test suite enforces this).
- **WHEN** a code path fetches a whitelisted internal URL (SSRF-exempt)
  **THE** source line **SHALL** carry an inline `# SSRF-EXEMPT: <reason>`
  comment AND the host SHALL be present in the guard module's
  whitelist constant.

## AC-11: Observability — structured rejection logs

- **WHEN** the SSRF guard rejects a URL **THE** service **SHALL** emit
  a structlog entry at level `warning` with stable fields:
  ```
  event="ssrf_blocked"
  url=<sanitised URL — no query string>
  hostname=<parsed host>
  reason=<"private_ip" | "link_local" | "loopback" | "docker_internal"
          | "non_https" | "no_hostname" | "dns_failed" | "dns_rebind">
  resolved_ips=<list or []>
  request_id=<uuid from RequestContextMiddleware>
  ```
- Verification: LogsQL query `event:"ssrf_blocked"` in VictoriaLogs
  returns at minimum one entry per test-triggered rejection during the
  E2E suite run.

## AC-12: Performance budget

- **WHEN** the test suite benchmarks `validate_url_pinned` against a
  cached hostname (cache hit) **THE** p95 added latency per crawl
  request **SHALL** be below 5 ms.
- **WHEN** the benchmark runs against a cold hostname (cache miss)
  **THE** p95 added latency **SHALL** be below 50 ms under normal DNS
  conditions (Docker embedded resolver cached, public authoritative
  in-region).
- Verification: pytest-benchmark test checked into
  `klai-knowledge-ingest/tests/test_url_validator_perf.py`. CI budget:
  the test fails if 50 ms is exceeded three runs in a row.

## AC-13: Defence-in-depth network isolation

- **WHEN** an operator runs from the `knowledge-ingest` container
  ```
  curl --connect-timeout 2 -s http://docker-socket-proxy:2375/v1.42/info
  ```
  **THE** command **SHALL** fail with connection error (network
  unreachable or connection refused). Same from the `crawl4ai`
  container.
- This is the current state on 2026-04-24 per research.md §4. AC-13 is
  a regression guard: any future compose edit adding these containers
  to `socket-proxy` MUST fail this check and therefore a CI smoke test.

## AC-14: VictoriaLogs 7-day zero-private-IP window

- **WHEN** the fix has been deployed for at least 7 consecutive days
  **THE** VictoriaLogs query
  ```
  service:knowledge-ingest AND event:"outbound_connect"
    AND resolved_ip:~"^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.|127\.|169\.254\.)"
  ```
  **SHALL** return zero hits. This is a monitoring success criterion,
  not an implementation gate — it is listed here so the deployment
  runbook includes the post-merge observation window.

---

## Test file layout

- `klai-knowledge-ingest/tests/test_url_validator.py` — extend existing
  file with rebinding (AC-5), docker-internal (AC-4), link-local
  coverage (AC-3). Unit scope.
- `klai-knowledge-ingest/tests/test_crawl_preview_ssrf.py` — new.
  Integration scope against a stub `crawl_page`. Covers AC-1, AC-6.
- `klai-knowledge-ingest/tests/test_url_validator_perf.py` — new.
  pytest-benchmark. Covers AC-12.
- `klai-portal/backend/tests/api/test_connectors_ssrf.py` — new.
  Covers AC-7, AC-8.
- `klai-connector/tests/test_sync_ssrf_regression.py` (or equivalent in
  the sync runner) — new. Covers AC-9.
- CI smoke test in `scripts/` (bash/python) executed post-deploy —
  covers AC-13 and AC-22 (extends to klai-connector).
- `klai-libs/image-storage/tests/test_url_guard.py` — new. Unit tests
  for `validate_image_url` (reject-list + IP-pinning). Covers the
  validator in isolation; feeds AC-15 through AC-18.
- `klai-connector/tests/test_sync_engine_images_ssrf.py` — new.
  Integration tests against each adapter (Notion / Confluence /
  GitHub / Airtable) with attacker-controlled internal URLs. Covers
  AC-15 through AC-18 at the adapter boundary.
- `klai-portal/backend/tests/api/test_confluence_ssrf.py` — new.
  Covers AC-19, AC-20.
- `klai-connector/tests/test_confluence_sync_legacy_base_url.py`
  (or equivalent in the connector sync runner) — new. Covers AC-21.

## AC-15: Notion adapter image pipeline rejects internal URLs

- **GIVEN** a connector adapter run that processes a Notion page whose
  `image` block has `external.url = "http://portal-api:8010/internal/v1/orgs"`
  **WHEN** `SyncEngine._upload_images` delegates to
  `download_and_upload_adapter_images` → `_download_validate_upload`
  **THEN** the shared `validate_image_url` guard **SHALL** reject the
  URL BEFORE `http_client.get(url)` is called
  **AND** the function **SHALL** return `None` for that image
  **AND** the document ingest for that Notion page **SHALL** continue
  (a single image failure never halts a document)
  **AND** a structured log **SHALL** be emitted with stable key
  `event="adapter_image_ssrf_blocked"` and fields `url`, `hostname`,
  `reason="docker_internal"`, `org_id`, `kb_slug`, `request_id`.
- Verification: unit test in
  `klai-connector/tests/test_sync_engine_images.py` that patches
  `http_client.get` to assert it is never called on the rejected URL.

## AC-16: Confluence adapter image pipeline rejects internal URLs

- **GIVEN** a Confluence page whose storage-format HTML contains
  `<ri:url ri:value="http://redis:6379/" />` inside an image tag
  **WHEN** the image pipeline attempts to download the referenced URL
  **THEN** `validate_image_url` **SHALL** reject it with
  `reason="docker_internal"` **AND SHALL NOT** issue an HTTP request
  to the Redis container
  **AND** the Confluence page text ingest **SHALL** still succeed.
- Verification: mock the Confluence storage HTML input, assert no
  outbound connection attempt to `redis:6379`, assert the page's
  other content is ingested normally.

## AC-17: GitHub adapter image pipeline rejects internal URLs

- **GIVEN** a markdown file `README.md` from a GitHub sync whose
  content is `![diagram](http://docker-socket-proxy:2375/info)`
  **WHEN** `_extract_markdown_images` emits the `ImageRef` and
  `SyncEngine._upload_images` tries to upload it
  **THEN** the URL **SHALL** be rejected by `validate_image_url`
  with `reason="docker_internal"` **AND SHALL NOT** reach
  `http_client.get(url)`
  **AND** the markdown body itself **SHALL** still be ingested (text
  content is unaffected by image-URL rejection).
- Verification: unit test patches `_image_http.get` as a spy and
  asserts zero calls for the rejected URL.

## AC-18: Airtable adapter image pipeline rejects internal URLs

- **GIVEN** an Airtable record whose attachment URL is
  `http://10.0.0.5/asset.png`
  **WHEN** `SyncEngine._upload_images` processes
  `DocumentRef.images` for that record
  **THEN** the URL **SHALL** be rejected with `reason="private_ip"`
  **AND** no outbound HTTP connection **SHALL** be attempted.
- Verification: same pattern — patch the shared `http_client.get`
  and assert it is never invoked for the RFC1918 URL.

## AC-19: Confluence connector config rejects non-Atlassian base_url

- **WHEN** the portal receives
  `POST /api/app/knowledge-bases/{kb}/connectors` with
  `connector_type="confluence"` AND
  `config.base_url="http://evil-but-resolves-internal.example.com/wiki"`
  **THE** portal **SHALL** return HTTP 422 with a pydantic validation
  error naming `config.base_url`
  **AND SHALL NOT** persist the connector row
  **AND SHALL NOT** construct an `atlassian.Confluence(...)` client.
- **WHEN** `config.base_url` is `https://attacker.example.com/wiki/`
  (HTTPS but not on `*.atlassian.net` or `*.atlassian.com`)
  **THE** portal **SHALL** return HTTP 422 with a domain allowlist
  error naming `config.base_url`.
- **WHEN** `config.base_url` is `https://10.0.0.5/wiki`
  **THE** portal **SHALL** return HTTP 422 with `reason="private_ip"`
  even though the domain allowlist step is structurally skipped for
  IP literals.
- **WHEN** `config.base_url` is `https://klai-tenant.atlassian.net`
  **THE** portal **SHALL** persist the connector normally (positive
  path — allowlist MUST NOT overblock legitimate tenants).
- Verification: new tests in
  `klai-portal/backend/tests/api/test_confluence_ssrf.py`.

## AC-20: Confluence connector update rejects SSRF-unsafe base_url

- **WHEN** `PUT /api/app/knowledge-bases/{kb}/connectors/{id}` is used
  to change a Confluence connector's `config.base_url` to
  `http://portal-api:8010/` (docker-internal)
  **THE** portal **SHALL** return HTTP 422
  **AND SHALL NOT** mutate the stored config
  **AND** the existing (unchanged) Confluence client
  configuration **SHALL** remain intact for subsequent sync runs.

## AC-21: Legacy Confluence connector sync refuses SSRF-unsafe base_url

- **GIVEN** a pre-existing `connector.connectors` row with
  `connector_type='confluence'` AND
  `config ->> 'base_url' = 'http://confluence-internal:8090/'`
  (stored before REQ-8 landed)
  **WHEN** a scheduled sync run loads and processes that connector
  **THEN** the sync run **SHALL** be marked failed with
  `error="ssrf_blocked_persisted_confluence_base_url"`
  **AND SHALL NOT** call `atlassian.Confluence(url=..., ...)`
  **AND** a structured log **SHALL** be emitted with stable key
  `event="confluence_base_url_blocked"`.
- Verification: insert a row with a docker-internal `base_url`,
  trigger the sync, assert the `sync_runs` row ends in
  `status='failed'` with the expected error, assert no Atlassian
  SDK client was instantiated (mock boundary assertion).

## AC-22: Defence-in-depth — connector NOT on socket-proxy network

- **WHEN** an operator runs from the `klai-connector` container
  ```
  curl --connect-timeout 2 -s http://docker-socket-proxy:2375/v1.42/info
  ```
  **THE** command **SHALL** fail with connection error (network
  unreachable or connection refused).
- Regression guard: any future edit to `deploy/docker-compose.yml`
  adding `klai-connector` to the `socket-proxy` network **SHALL**
  fail a CI smoke test that runs the curl check above.
- This extends AC-13's list from `{knowledge-ingest, crawl4ai}` to
  `{knowledge-ingest, crawl4ai, klai-connector}`. The post-merge
  smoke-test script is updated accordingly.

## AC-23: IP-pinning applies to the image pipeline

- **WHEN** `validate_image_url("https://rebinder.example.test/icon.png")`
  resolves to `1.1.1.1` on the first lookup AND subsequent DNS
  returns `172.17.0.5`
  **THEN** `_image_http.get(...)` **SHALL** connect to `1.1.1.1`
  (the pinned IP) **AND SHALL NOT** connect to `172.17.0.5`.
- Verification: monkey-patch `socket.getaddrinfo`; assert the httpx
  transport's actual connect target is the pinned IP.
- Regression guard for REQ-7.4: if the `_image_http` factory ever
  stops configuring `PinnedResolverTransport`, this test fails.

---

## Out-of-scope for this SPEC's acceptance

- crawl4ai's own fetcher behaviour on rebinding (AC-5 tests
  knowledge-ingest's own httpx fetches and the rewrite-before-submit
  path for crawl4ai; verifying crawl4ai internals is not this SPEC's
  job).
- Host-level egress firewall (REQ-5 / AC-13 only asserts the compose
  file; iptables DOCKER-USER rules are a separate infra SPEC).
- Port scanning detection / anomaly alerting (downstream Grafana work
  can build on AC-11's stable log keys).
