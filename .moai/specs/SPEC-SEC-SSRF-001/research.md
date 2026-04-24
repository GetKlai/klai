# Research — SPEC-SEC-SSRF-001

## 1. Finding context

Audit: Cornelis Poppema, 2026-04-22 (external adversarial review).
Verification pass: Claude Opus, 2026-04-24, verdict per finding captured in
[SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md).

Three SSRF primitives plus one chained exploit:

- **#6 — preview_crawl:** `POST /ingest/v1/crawl/preview` does not call
  `validate_url`. Attacker posts an internal URL and knowledge-ingest /
  crawl4ai fetches it. VERIFIED at `crawl.py:125-223`.
- **#7 — persisted web_crawler connector:** `WebcrawlerConfig` accepts any
  `base_url` string; no SSRF check in the pydantic validator. A scheduled
  sync then fetches the URL. VERIFIED at `connectors.py:81-149`.
- **#8 — DNS-rebinding TOCTOU:** `validate_url` resolves once, returns
  the unchanged URL, and the subsequent HTTP client re-resolves DNS.
  VERIFIED at `url_validator.py:34-68`.
- **#A1 — chain to docker-socket-proxy:** an SSRF primitive that can
  reach an internal hostname reachable from the fetching container can
  leak environment variables via the `/v1.42/containers/{id}/json`
  endpoint on docker-socket-proxy. VERIFIED as a chain of #6/#7.

## 2. Inventory of URL-consuming call sites

Grepped `httpx.AsyncClient`, `crawl_site`, `crawl_page`, `crawl_dom_summary`,
`validate_url` across klai-knowledge-ingest and klai-portal.

### klai-knowledge-ingest

| File | Line | Call | Guarded today? | Notes |
|---|---|---|---|---|
| `routes/crawl.py` | 125–223 | `preview_crawl(body)` → `_run_crawl(body.url, ...)` → `crawl_page(url, ...)` | **NO** | Finding #6. Broad `except` at 221 swallows any error into a 200-empty response. |
| `routes/crawl.py` | 147 | `crawl_dom_summary(body.url)` inside preview's AI selector branch | **NO** | Same URL, second crawl path. Independent httpx client. |
| `routes/crawl.py` | 198 | `crawl_dom_summary(body.url)` inside auth_guard branch | **NO** | Third crawl of the same URL. |
| `routes/crawl.py` | 235 | `validate_url(request.url)` before `_run_crawl` | **YES (scheme+IP, but TOCTOU)** | `crawl_url` is the only endpoint that calls `validate_url`. |
| `routes/crawl.py` | 261 | `_run_crawl(request.url, effective_selector)` | Relies on line 235 guard | Same URL, re-resolved by crawl4ai. |
| `routes/crawl_sync.py` | (whole file) | `POST /ingest/v1/crawl/sync` | **NO** | Scans required during /moai run; tracker classifies as part of SPEC-CRAWL-004 but shares the same SSRF surface. Added here so it is not forgotten. |
| `crawl4ai_client.py` | 167 | `_fetch_sitemap_urls(base_url)` inside `crawl_site` (supplements BFS) | **NO** | `base_url` is the attacker-controlled `start_url`. Fetches `{base_url}/sitemap.xml` via httpx. |
| `crawl4ai_client.py` | 184 | `_crawl_sync(client, payload)` | Client target is internal crawl4ai; URL in payload is user-supplied | The user URL is sent inside the crawl4ai JSON body — crawl4ai's browser resolves it. |
| `crawl4ai_client.py` | 361 | `crawl_site` → `POST {crawl4ai_api_url}/crawl/job` | Same as above | BFS start_url comes from connector config. |
| `crawl4ai_client.py` | 424 | `crawl_page(u, selector=selector)` for each sitemap-supplemented URL | **NO** | Each supplementary URL is fetched individually. |
| `crawl4ai_client.py` | 483 | `crawl_dom_summary(url)` for AI selector detection | **NO** | Same class as crawl_page. |
| `adapters/crawler.py` | 145 | `run_crawl_job(..., start_url, ...)` → `crawl_site(start_url, ...)` | **NO** | `start_url` originates from the persisted `web_crawler.base_url`. |

### klai-portal

| File | Line | Call | Guarded today? | Notes |
|---|---|---|---|---|
| `app/api/connectors.py` | 81–149 | `WebcrawlerConfig` pydantic model | **NO SSRF check** | Validates `canary_url` prefix against `base_url` only. Finding #7. |
| `app/api/connectors.py` | 29–57 | `_auto_fill_canary_fingerprint` → `klai_connector_client.compute_fingerprint(canary_url, cookies)` | **NO** | Portal delegates fingerprint computation to klai-connector, which fetches `canary_url`. |
| `app/services/klai_connector_client.py` | — | `compute_fingerprint` HTTP call | Internal target | Not user-URL-consuming itself; the user URL travels inside the JSON body. |

## 3. Current state of `validate_url` and its consumers

File: [`klai-knowledge-ingest/knowledge_ingest/utils/url_validator.py`](../../../klai-knowledge-ingest/knowledge_ingest/utils/url_validator.py)

Current signature:

```python
async def validate_url(url: str, dns_timeout: float = 2.0) -> str
```

Returns: the input URL, unchanged, on success. Raises `ValueError` on
rejection. **Does not return the resolved IPs.** This is the entire
TOCTOU bug (Finding #8): the caller receives a URL, constructs an httpx
client with that URL, and httpx re-resolves DNS. Between the two
resolutions the attacker-controlled authoritative DNS can change the
answer from `1.1.1.1` to `172.17.0.5`. `validate_url` sees the safe
answer and the fetch gets the unsafe one.

Consumers (grep `validate_url`, excluding tests):

- `routes/crawl.py:34` — imports
- `routes/crawl.py:235` — the sole production call site
- `tests/test_crawl_registry_dedup.py:180,210` — mocked
- `tests/test_crawl_link_fields.py:54,100,139` — mocked
- `tests/test_url_validator.py:*` — direct unit tests (scheme, private,
  loopback, link-local, DNS-timeout). No rebinding test exists.

## 4. Docker network topology (verified against `deploy/docker-compose.yml`)

Networks declared (lines 1–45):

- `klai-net` — shared bridge for all application services
- `socket-proxy` — `internal: true`, reserved for `docker-socket-proxy`
  and the services allowed to talk to it
- `net-postgres`, `net-mongodb`, `net-redis`, `net-meilisearch` —
  backing-store isolation

Membership table for this SPEC's scope:

| Container | Networks | Can reach docker-socket-proxy? |
|---|---|---|
| `portal-api` | `klai-net`, `net-postgres`, `net-mongodb`, `net-redis`, `socket-proxy` | **YES** — needed for tenant provisioning |
| `docker-socket-proxy` | `socket-proxy` | — |
| `knowledge-ingest` | `klai-net`, `net-postgres` | NO |
| `crawl4ai` | `klai-net` | NO |
| `klai-connector` | `klai-net`, `net-postgres` | NO |
| `runtime-api-socket-proxy` (socat) | `socket-proxy`, `klai-net` (implied via named volume) | Bridges socket-proxy into a Unix socket; not a network path |

Verification commands (run before any SPEC-SEC-SSRF-001 PR merges, and
after, to confirm no drift):

```bash
# From knowledge-ingest: must not reach docker-socket-proxy
docker exec klai-core-knowledge-ingest-1 \
  curl --connect-timeout 2 -s http://docker-socket-proxy:2375/v1.42/info
# Expected: connection error (network unreachable / no route)

# From crawl4ai: must not reach docker-socket-proxy
docker exec klai-core-crawl4ai-1 \
  curl --connect-timeout 2 -s http://docker-socket-proxy:2375/v1.42/info
# Expected: connection error
```

This is already the current state on 2026-04-24. REQ-5 codifies it as a
guardrail.

## 5. DNS resolution flow from validate_url to crawl4ai

Current control flow (for `POST /ingest/v1/crawl` — the protected path):

```
Client POST url=https://attacker.example.com/
           │
           ▼  routes/crawl.py:235
     validate_url(url)
           │  socket.getaddrinfo("attacker.example.com")
           │  → [1.1.1.1]         (1st resolution — accepted)
           │  returns url unchanged
           ▼
     _run_crawl(url, selector)
           │
           ▼  crawl4ai_client.py:254
     httpx.AsyncClient().post(
       "http://crawl4ai:11235/crawl",
       json={"urls": ["https://attacker.example.com/"], ...}
     )
           │  (crawl4ai receives the string)
           ▼
     crawl4ai container → Playwright browser
           │  browser DNS lookup: "attacker.example.com"
           │  → [172.17.0.5]      (2nd resolution — attacker rebound)
           ▼
     TCP connect to 172.17.0.5:443
           ▼
     TLS SNI=attacker.example.com, cert mismatch → depends on server
```

The second resolution happens inside the **crawl4ai container**, not
knowledge-ingest. That is why a naive "pin the IP on knowledge-ingest's
httpx transport" does not fully close the hole — knowledge-ingest does
not make the outbound fetch on the user URL; crawl4ai does. The
mitigation must therefore either (a) rewrite the URL's host to the
pinned IP literal before submission to crawl4ai, or (b) validate once
and accept that crawl4ai's own DNS is the final authority.

## 6. Proposed IP-pinning mechanism

### 6.1 New contract

```python
@dataclass(frozen=True)
class ValidatedURL:
    url: str                     # original URL, unchanged
    hostname: str                # parsed hostname
    pinned_ips: frozenset[str]   # all IPs the guard resolved and accepted
    preferred_ip: str            # IP to use for the subsequent connection

async def validate_url_pinned(url: str) -> ValidatedURL: ...
```

### 6.2 Custom httpx transport for local fetches

Where knowledge-ingest fetches directly (e.g. `_fetch_sitemap_urls`), use
a custom transport that refuses to re-resolve:

```python
class PinnedResolverTransport(httpx.AsyncHTTPTransport):
    def __init__(self, pinned: dict[str, str], **kwargs):
        super().__init__(**kwargs)
        self._pinned = pinned  # host → ip

    async def handle_async_request(self, request):
        host = request.url.host
        if host in self._pinned:
            # rewrite URL's host to the pinned IP; keep Host header original
            ip = self._pinned[host]
            request.url = request.url.copy_with(host=ip)
            request.headers.setdefault("Host", host)
        return await super().handle_async_request(request)
```

This pattern works because httpx (like aiohttp, unlike requests' `verify`)
keeps SNI from the URL host string, not from the Host header. By
replacing the URL host with the IP literal, TCP connects to the pinned
IP; TLS SNI becomes the IP (which likely does not match any cert). To
keep SNI = original hostname, we pass `server_hostname=hostname` via the
transport's SSL context. The canonical pattern is documented in
[httpx issue #2180](https://github.com/encode/httpx/issues/2180) and
used in production elsewhere at Klai (internal service-to-service mTLS
uses the same trick).

### 6.3 For crawl4ai-delegated fetches

knowledge-ingest does not own the DNS resolver that crawl4ai uses.
Three options, ranked:

1. **URL rewrite + Host header (REQ-3.3, preferred)**
   ```python
   v = await validate_url_pinned(user_url)
   pinned_url = f"https://{v.preferred_ip}{parsed.path or ''}"
   payload["urls"] = [pinned_url]
   payload["extra_headers"] = {"Host": v.hostname}
   # Configure crawl4ai to send SNI=hostname via playwright's
   # newContext({ extraHTTPHeaders }) and the TLS serverName hint.
   ```
   Requires verifying crawl4ai honours `extra_headers` on the playwright
   navigation path. A spike in Plan phase: POST a contrived URL with
   mismatched Host header and check if crawl4ai forwards it correctly.
2. **Pre-resolve and refuse on multi-IP ambiguity.** If
   `validate_url_pinned` returns more than one IP (round-robin DNS),
   refuse the URL. This is a tighter but less user-friendly option;
   keeping as a fallback only.
3. **Fail closed on unknown resolver behaviour (REQ-3.5).** If option 1
   cannot be verified to hold and option 2 is too strict, the SPEC
   refuses the URL rather than fetch it. This is the explicit fail-open
   prevention that the constitution demands.

### 6.4 Cache

`validate_url_pinned` SHOULD cache resolved IP sets per hostname with a
60 s TTL and a bounded LRU (1024 entries). This is the only way to meet
the 50 ms p95 budget while keeping the guard on every request. The
cache key is the hostname, not the URL.

## 7. Reference: existing SSRF-adjacent patterns in the codebase

- `validate_url_scheme` (HTTPS-only) at `url_validator.py:12` — kept,
  unchanged.
- `is_private_ip` at `url_validator.py:19` — kept; extend to also treat
  container-name-resolvable hostnames as private (see REQ-1.3 list).
- `klai-portal/backend/app/api/webhooks.py` — does NOT fetch URLs;
  receives webhooks. Out of scope.
- `klai-portal/backend/app/services/portal_client.py` — internal
  service-to-service. No user URL. Out of scope.
- `klai-portal/backend/app/services/klai_connector_client.py:compute_fingerprint` —
  relays a user URL to klai-connector. Covered indirectly by REQ-2
  since the user URL cannot be persisted without passing the validator.

## 8. Integration with observability stack

Per [.claude/rules/klai/infra/observability.md](../../../.claude/rules/klai/infra/observability.md):

- structlog JSON to stdout → Alloy → VictoriaLogs (30 d retention)
- `request_id` bound by `RequestContextMiddleware`
- Stable event keys allow LogsQL alerting

This SPEC adds one stable event key: `event="ssrf_blocked"`. Grafana
alert candidate: `event:"ssrf_blocked" | stats count by service,
reason` over 1 h — non-zero means either an attack or a bug in a
caller passing an obviously-invalid URL. Both are worth a pager.

## 9. Open questions (tracked, not blocking)

- Does crawl4ai propagate `extra_headers` to the TLS handshake's SNI,
  or only to the HTTP request headers? To verify during /moai plan
  via a direct spike: POST a URL whose path is `https://1.1.1.1/` with
  `extra_headers={"Host": "example.com"}` and observe what the target
  sees. If the answer is "HTTP Host only, SNI=IP", option 1 in §6.3
  fails and we fall to option 2.
- Should the 60 s DNS cache be persisted in Redis for multi-worker
  correctness? Currently knowledge-ingest runs a single worker per
  container; process-local LRU is sufficient. If the service scales
  horizontally, revisit.
- Is there a hostname rewriting gotcha for IPv6 (bracketed vs
  unbracketed)? Yes — `https://[::1]/path` vs `https://::1/path`;
  httpx requires brackets. `validate_url_pinned` must emit brackets
  for IPv6 `preferred_ip`.

## 10. Evidence summary for the A1 chain

The A1 audit verdict is "VERIFIED (chain)". The precise chain, with
references, is:

1. Attacker authenticates (any tenant user).
2. Attacker posts `{"url": "http://portal-api:8010/internal/v1/..."}` to
   `/ingest/v1/crawl/preview`. `preview_crawl` does not call
   `validate_url` (crawl.py:125). The URL is passed to `crawl_page`,
   then to crawl4ai.
3. crawl4ai is on `klai-net`. `portal-api:8010` resolves on that
   network via Docker's embedded DNS. crawl4ai fetches it.
4. Portal-api's `/internal/*` endpoints require `INTERNAL_SECRET`, so
   this step does not leak anything *by itself*. But the same primitive
   lets the attacker aim at any other container on `klai-net` whose
   name resolves and whose service returns information — for example
   `http://docker-socket-proxy:2375/v1.42/containers/json` if
   crawl4ai is ever added to `socket-proxy`. Today crawl4ai is NOT on
   `socket-proxy` (verified), which is why A1 is a chain of #6/#7
   rather than an immediate exploit.
5. If the attacker can reach **any** container on `socket-proxy` from
   **any** container on `klai-net` via a future compose misstep, A1
   becomes immediate. REQ-5 is the guardrail.

Net: the single most dangerous property is that `preview_crawl`
accepts a URL without validation. Everything else is amplification.
Fix REQ-1 and A1 loses its first link, regardless of REQ-5.

---

## 11. Internal-wave additions (2026-04-24)

The v0.2.0 analysis was scoped to the knowledge-ingest + portal slice.
The internal audit wave extended scans to the connector surface and
surfaced two independent SSRF primitives that do not touch the crawl
pipeline at all.

### 11.1 klai-connector image-flow call graph

Every connector adapter that extracts images hands them off to the
same choke point. The chain is:

```
SyncEngine._execute_sync(connector_id)
        │  klai-connector/app/services/sync_engine.py
        │
        ▼  adapter.list_documents / get_document
   DocumentRef(..., images=[ImageRef(url=..., alt=..., source_path=...)])
        │
        │  Adapter URL sources (all unauthenticated to the image host):
        │    Notion:     _extract_image_blocks           notion.py:397-415
        │                (external.url / file.url)
        │    Confluence: _extract_images_from_storage    confluence.py:341-417
        │                (<ri:url ri:value=".../>)
        │    GitHub:     _extract_markdown_images        github.py:209-211
        │                (markdown ![alt](url))
        │    Airtable:   attachment URLs surfaced via DocumentRef.images
        │
        ▼  SyncEngine._upload_images  sync_engine.py:607
   download_and_upload_adapter_images(
       http_client=self._image_http,     # httpx.AsyncClient(timeout=30.0)
       image_store=...,
       markdown_pairs=..., parsed_images=...,
       image_url_pairs=[(alt, ref.url) for ref in doc_ref.images],
   )
        │  klai-libs/image-storage/klai_image_storage/pipeline.py
        │
        ▼  _download_validate_upload  pipeline.py:73-128
   resp = await http_client.get(url)    #  ← UNGUARDED FETCH
        │
        │  Only gate below this line is magic-byte content check.
        │  By the time it runs, the request has already hit the
        │  internal host and returned.
        │
        ▼  image_store.validate_image(data)   # filters STORAGE, not FETCH
        ▼  image_store.upload_image(...)       # only on valid payload
```

Key observations:

- `self._image_http = httpx.AsyncClient(timeout=30.0)`
  (sync_engine.py:85) is constructed with **no** `auth=`, **no**
  cookie jar, **no** default headers. This is what keeps Finding I
  from being a credential-theft SSRF — it is blind/timing only. It
  does still fingerprint the internal topology:
  - `http://portal-api:8010/health` → 200 OK → service is up
  - `http://redis:6379/` → protocol error but TCP handshake
    succeeded → redis container exists
  - `http://docker-socket-proxy:2375/` → depends on future compose
    drift; today the connector container cannot reach it
- `validate_image(data)` at pipeline.py:116 uses `filetype`
  magic-byte detection. An attacker who wants content returned can
  still point at any URL that serves a PNG/JPEG header — e.g. an
  attacker-controlled public host that redirects to an internal
  address. Because `httpx` follows redirects by default, the fetch
  will hit the redirect target; the magic-byte check will reject the
  bytes but only AFTER the internal request has completed. The
  guard is too late.
- The shared library is also used by knowledge-ingest's crawl
  pipeline (`download_and_upload_crawl_images`). A guard placed here
  covers both flows. This is the single-source-of-truth argument
  for REQ-7's location.

### 11.2 Confluence SDK threat model

`atlassian.Confluence(url=base_url, cloud=True)` — the `cloud=True`
flag is often read as "this enforces Atlassian Cloud". It does not.
Reading the SDK source (`atlassian-python-api/atlassian/confluence.py`):

- `cloud=True` switches the internal path prefix from `/rest/api`
  (server/data-center) to `/wiki/rest/api`.
- The `url=` argument is used verbatim as the base for every REST
  call; it is never validated against `atlassian.net`.
- Authentication is Basic auth with the tenant-supplied
  `username` + `password` (API token). Every call includes the
  Authorization header, so a redirect from `base_url` to an internal
  host causes the SDK to forward Basic auth unless the caller
  explicitly sets `allow_redirects=False` (the SDK does not).

Attack shape:

```
attacker posts:
  POST /api/app/knowledge-bases/{kb}/connectors
  body:
    connector_type: confluence
    config:
      base_url: https://evil.example.com/wiki/      # attacker-owned
      email: attacker@tenant.example
      api_token: ATLASSIAN_TOKEN_ATTACKER_PROVIDED

portal persists config; scheduled sync fires later
confluence.py:_build_confluence_client:
  Confluence(url="https://evil.example.com/wiki/",
             username=email, password=api_token, cloud=True)

Confluence SDK issues e.g.
  GET https://evil.example.com/wiki/rest/api/space
  headers: Authorization: Basic <email:token base64>

evil.example.com responds with 302 → http://portal-api:8010/internal/...
SDK follows redirect → internal call made → Authorization header
forwarded (Basic auth of attacker-controlled token, so no internal
secret leaks; the real risk is probing internal endpoints).

If attacker provides a credential they already control
(atlassian account under their own control) and points base_url
at http://portal-api:8010/... directly, they get blind SSRF via
the Confluence sync path.
```

Because a redirect chain off a public host is indistinguishable
from a sideways SSRF at `base_url`, the right place to stop it is
at config-save time, before any HTTP call. REQ-8's allowlist at the
pydantic validator is the canonical mitigation. A second guard at
sync-run time (REQ-8.4) handles legacy rows that predate the
validator, mirroring REQ-2.4's approach for `WebcrawlerConfig`.

### 11.3 Proposed shared guard location

`klai-libs/image-storage/` is already a shared library; adding a
new module there gives both current consumers (klai-connector,
klai-knowledge-ingest) the guard for free:

```
klai-libs/image-storage/klai_image_storage/
  __init__.py
  pipeline.py          # existing — _download_validate_upload
  storage.py           # existing — ImageStore, MAX_IMAGE_SIZE
  utils.py             # existing — dedupe, is_valid_image_src
  url_guard.py         # NEW — validate_image_url()
```

`url_guard.validate_image_url(url)` SHOULD:

1. Parse URL, reject non-HTTPS scheme (same as REQ-1.3 for public
   image sources — internal image sources are always rejected; if
   a future internal image source is needed, it must be SSRF-exempt
   by explicit whitelist comment, not by loosening this guard).
2. Resolve hostname via `socket.getaddrinfo` (with the DNS cache from
   REQ-3.1's `validate_url_pinned`), reject RFC1918 / loopback /
   link-local / docker-internal.
3. Return `ValidatedURL` with `pinned_ips` / `preferred_ip` so the
   downstream `http_client.get(url)` can use the IP-pinned transport
   (REQ-3.1 mechanism re-used, not re-implemented).

The transport used by `_image_http` in sync_engine.py SHOULD be
replaced with `PinnedResolverTransport` (research.md §6.2) so that
all existing and future adapters inherit rebinding defence without
per-call code.

### 11.4 Confluence validator placement

The portal's Confluence config validator should live alongside the
existing `WebcrawlerConfig` validator — same file, same placement:

```python
# klai-portal/backend/app/api/connectors.py (new class)

class ConfluenceConfig(BaseModel):
    base_url: str
    email: EmailStr
    api_token: SecretStr
    space_keys: list[str] = []

    @model_validator(mode="after")
    def _validate_base_url(self) -> "ConfluenceConfig":
        # REQ-8.1 + REQ-8.2: allowlist + private/docker reject
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            raise ValueError("base_url must use HTTPS")
        host = (parsed.hostname or "").lower()
        if not (host.endswith(".atlassian.net") or host.endswith(".atlassian.com")):
            raise ValueError(
                "base_url must be on *.atlassian.net or *.atlassian.com"
            )
        # Also SSRF-check in case DNS returns RFC1918 (parked atlassian
        # subdomain etc.). Reuse the portal's central SSRF guard.
        validate_url_pinned_sync(self.base_url)
        return self
```

This matches REQ-2.3's pattern and means every current and future
API path handling Confluence configs inherits the check.

### 11.5 Compose-time guardrail extension

`klai-connector` is on `klai-net` + `net-postgres` today (verified
against `deploy/docker-compose.yml`). This is what keeps Finding I
from being an immediate env-dump chain. REQ-5's
"must-not-join-socket-proxy" enumeration (and the rule file at
`.claude/rules/klai/platform/docker-socket-proxy.md`) must be updated
to include `klai-connector` explicitly, alongside the existing entries
for knowledge-ingest / crawl4ai / mailer / scribe / research-api /
retrieval-api / klai-knowledge-mcp / klai-focus.

The smoke test from §4 SHOULD be extended with the connector check:

```bash
docker exec klai-core-klai-connector-1 \
  curl --connect-timeout 2 -s http://docker-socket-proxy:2375/v1.42/info
# Expected: connection error (network unreachable / no route)
```

This goes into the same post-merge smoke-test script that §4's
commands already live in.

### 11.6 Open question (tracked, not blocking)

- Does `httpx.AsyncClient(...).get(url)` with a redirect chain
  still honour the IP-pinned transport for the redirect target?
  By default yes (httpx reuses the transport), but a one-line test
  should be added to the test suite to confirm. If not, the guard
  must be called a second time on the post-redirect URL — or
  `follow_redirects=False` set on `self._image_http`, with redirect
  chasing moved into application code so each hop goes through the
  guard.
