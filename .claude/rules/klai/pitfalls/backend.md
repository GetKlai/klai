---
paths:
  - "**/*.py"
  - "**/pyproject.toml"
---
# Backend Pitfalls

> Python async services (FastAPI, httpx, asyncio) in klai-mono.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [backend-async-sequential-loop](#backend-async-sequential-loop) | MED | Use `asyncio.gather`, not `await` in a for loop |
| [backend-async-no-per-call-timeout](#backend-async-no-per-call-timeout) | MED | Always set `timeout=` on external httpx calls |
| [backend-config-default-vs-env](#backend-config-default-vs-env) | LOW | Config defaults should not silently override env vars |
| [backend-prometheus-global-registry-tests](#backend-prometheus-global-registry-tests) | HIGH | Never use the global prometheus_client registry in tests |
| [backend-sendbeacon-no-auth-header](#backend-sendbeacon-no-auth-header) | HIGH | `navigator.sendBeacon` cannot send Authorization headers — design the endpoint as intentionally unauthenticated |
| [backend-crawl4ai-class-substring-selectors](#backend-crawl4ai-class-substring-selectors) | HIGH | Never use `[class*="sidebar"]` in JS DOM removal — substring selectors match layout wrappers and delete article content |
| [backend-fastapi-required-field-breaks-callers](#backend-fastapi-required-field-breaks-callers) | MED | Adding a required field to a FastAPI request model breaks existing callers — use optional with a guard instead |
| [backend-silent-error-swallowing](#backend-silent-error-swallowing) | HIGH | Always log actual error details (status code, response body) before returning a generic user message |
| [backend-ruff-catches-refactor-bugs](#backend-ruff-catches-refactor-bugs) | HIGH | Run `ruff check` after each refactor step — F821/F401 catch real runtime bugs |

---

## backend-async-sequential-loop

**Severity:** MEDIUM

**Problem:** `await` calls inside a `for` loop execute sequentially. When fetching multiple external resources (URLs, API calls), total latency is the sum of all individual calls.

```python
# WRONG — sequential, latency = sum(all fetches)
for url in urls:
    result = await fetch(url)   # waits for each before starting the next
    results.append(result)
```

**Fix:** Use `asyncio.gather` to run all fetches in parallel:

```python
# CORRECT — parallel, latency = max(all fetches)
results = await asyncio.gather(*[fetch(url) for url in urls])
```

**Seen in:** `klai-focus/research-api` web mode — 5 sequential docling URL fetches caused 25-50s latency. After parallelising: ~5-10s.

---

## backend-async-no-per-call-timeout

**Severity:** MEDIUM

**Problem:** `asyncio.gather` runs tasks in parallel but still waits for all of them to finish. If one external call is slow (e.g. 60s httpx timeout), the entire gather blocks until that task times out.

```python
# RISKY — one slow URL blocks the whole gather for up to 120s
results = await asyncio.gather(*[docling.convert_url(url) for url in urls])
```

**Fix:** Wrap each task with `asyncio.wait_for` to enforce a per-call deadline:

```python
_TIMEOUT = 15.0  # seconds per call

async def _fetch(url: str):
    try:
        return await asyncio.wait_for(docling.convert_url(url), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout fetching: %s", url)
        return None

results = await asyncio.gather(*[_fetch(url) for url in urls])
```

The outer httpx/service timeout (e.g. 120s) is a safety net — the `wait_for` is the real deadline.

**Seen in:** `klai-focus/research-api` web mode — added `_WEB_URL_TIMEOUT = 15.0` after parallelising.

---

## backend-config-default-vs-env

**Severity:** LOW

**Problem:** A wrong default in `pydantic-settings` `BaseSettings` is silently masked by an env var override in production. The bug only surfaces in fresh deployments that don't have the env var set.

```python
# WRONG default — masked in production by SEARXNG_URL=http://searxng:8080
searxng_url: str = "http://searxng:8888"
```

**Fix:** Always set the default to the real production value. Verify by checking the actual service port (`docker ps`) before writing the default.

**Seen in:** `klai-focus/research-api` config — default port was 8888, actual container port 8080.

---

## backend-prometheus-global-registry-tests

**Severity:** HIGH

**Problem:** `prometheus_client` uses a global `REGISTRY` by default. If two tests register the same metric name, the second test raises `ValueError: Duplicated timeseries`. Tests also bleed state into each other — counters from test A affect assertions in test B.

```python
# WRONG — uses global registry, breaks on second test run
lcp = Histogram("webvitals_lcp_seconds", "LCP", ["page", "rating"],
    buckets=(...))
```

**Fix:** Use a dedicated `CollectorRegistry` per instantiation, wrapped in a dataclass. Patch the module-level instance in tests via `autouse` fixture.

```python
# vitals.py — dedicated registry per instance
from dataclasses import dataclass, field
from prometheus_client import CollectorRegistry, Histogram

@dataclass
class VitalsMetrics:
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    lcp: Histogram = field(init=False)

    def __post_init__(self) -> None:
        self.lcp = Histogram("webvitals_lcp_seconds", "LCP", ["page", "rating"],
            buckets=(...), registry=self.registry)

_metrics = VitalsMetrics()  # module-level singleton
```

```python
# test_vitals.py — fresh instance per test via autouse patch
import pytest
from unittest.mock import patch
from app.api import vitals as vitals_mod
from app.api.vitals import VitalsMetrics

@pytest.fixture(autouse=True)
def _fresh_metrics():
    fresh = VitalsMetrics()
    with patch.object(vitals_mod, "_metrics", fresh):
        yield
```

**Seen in:** `klai-portal/backend/app/api/vitals.py` — SPEC-PERF-001 Web Vitals pipeline.

---

## backend-sendbeacon-no-auth-header

**Severity:** HIGH

**Trigger:** Building a browser → backend analytics or monitoring endpoint that requires authentication

`navigator.sendBeacon` is the correct API for sending telemetry on page unload (`visibilitychange → hidden`). It cannot set custom headers — including `Authorization`. Any backend endpoint that requires a Bearer token will reject every beacon request with 401.

**Why it happens:**
`sendBeacon` is a fire-and-forget, non-blocking API designed for the page-close scenario. It deliberately does not support `fetch`-style request options (no headers, no credentials override). The browser controls the request.

**Prevention:**
1. Design analytics/vitals ingest endpoints as intentionally unauthenticated. Accept this and document it explicitly in the router.
2. Add rate limiting at the reverse proxy (Caddy) as the primary protection against abuse:
   ```
   # Caddy — 60 req/min per IP for the vitals endpoint
   rate_limit /api/vitals 60r/m
   ```
3. Validate and clamp incoming values at the FastAPI layer (Pydantic `Field(ge=0, le=60000)`) to limit damage from bad data.
4. If you need authentication, use `fetch()` instead (with `keepalive: true` as a substitute for sendBeacon reliability) — but note that `keepalive` requests are dropped if the payload exceeds 64 KB.

**Correct pattern:**
```python
# vitals.py — intentionally unauthenticated; rate limiting is the only guard
@router.post("/api/vitals", status_code=204)
async def ingest_vitals(
    metrics: Annotated[list[VitalMetric], Body(max_length=10)],
) -> Response:
    """Receive a batch of Web Vitals metrics from the browser.
    Unauthenticated by design: navigator.sendBeacon cannot set headers.
    Rate limited at the Caddy layer.
    """
    ...
```

**Seen in:** `klai-portal/backend/app/api/vitals.py` — SPEC-PERF-001 Web Vitals pipeline.

**See also:** MDN sendBeacon docs, `patterns/platform.md#platform-caddy-tenant-routing` for Caddy config

---

## backend-crawl4ai-class-substring-selectors

**Severity:** HIGH

**Trigger:** Writing JS DOM removal selectors for crawl4ai (or any headless browser scraper) that strip navigation/chrome elements before content extraction

Substring class selectors like `[class*="sidebar"]` or `[class*="nav"]` match layout wrapper elements whose class lists contain those strings incidentally — e.g. `class="has-sidebar"` or `class="main-nav-wrapper"`. Removing the wrapper removes the article body inside it, producing `raw_words=0`.

**Why it happens:**
CSS attribute substring matching (`*=`) is greedy. A `<main class="layout has-sidebar">` wrapping the entire article content will match `[class*="sidebar"]` and be deleted along with everything inside it.

**Prevention:**
1. Use only semantic element selectors and ARIA role selectors for chrome removal — never substring class/id selectors:
   ```js
   // SAFE — structural semantics, unambiguous
   const REMOVE = ['nav', 'header', 'footer', 'aside',
                   '[role="navigation"]', '[role="banner"]',
                   '[role="contentinfo"]', '[role="complementary"]'];
   ```
2. Never use `[class*="..."]`, `[id*="..."]`, `[class^="..."]`, or `[class$="..."]` in JS removal scripts.
3. After any crawl config change, spot-check `raw_words` on a known-good page before deploying.

**Seen in:** `klai-knowledge-ingest/knowledge_ingest/routes/crawl.py` — `_JS_REMOVE_CHROME` originally included `[class*="sidebar"]`, which zeroed out word counts on sites with `has-sidebar` wrapper classes. Fixed in SPEC-CRAWL-001.

**See also:** `patterns/backend.md#backend-two-phase-crawl-ai-fallback`

---

## backend-fastapi-required-field-breaks-callers

**Severity:** MED

**Trigger:** Adding a new field to an existing FastAPI Pydantic request model that is called by a frontend or other service you do not control in the same deploy

Making the field required (`field: str`) will cause all existing callers that don't send it to receive a `422 Unprocessable Entity`. In a monorepo where frontend and backend deploy independently, this creates a window where the old frontend breaks against the new backend.

**Why it happens:**
Pydantic V2 treats any field without a default as required. A missing required field fails validation before the endpoint body runs.

**Prevention:**
1. Add new fields as optional with a safe default: `field: str = ""`
2. Guard usage with an explicit check rather than relying on truthiness alone when empty string is a valid value:
   ```python
   # CORRECT — degrades gracefully when caller omits org_id
   org_id: str = ""

   # in the endpoint:
   if body.org_id:
       stored = await domain_selectors.get(body.org_id, domain)
   ```
3. Only make a field required in a new endpoint, or when you can guarantee a coordinated deploy of all callers.

**Seen in:** `klai-knowledge-ingest` `CrawlPreviewRequest` — adding `org_id` for per-tenant domain selector lookup (SPEC-CRAWL-001). Made optional so existing frontend callers without `org_id` continue to work; domain features degrade silently.

---

## backend-silent-error-swallowing

**Severity:** HIGH

**Trigger:** Writing error handling in MCP tools, service integrations, or any code that returns a generic user-facing error message

Returning a generic error message (e.g. `"An error occurred"`) to the caller without first logging the actual HTTP status code, response body, and context variables makes debugging impossible. The tool just says "error occurred" with no trace in logs — you cannot distinguish a 403 from a 500 from a network timeout.

**Why it happens:**
Developers focus on the user experience (clean error message) and forget that someone will need to debug the failure later. The actual error details are available in the exception but discarded before the generic message is returned.

**Wrong:**
```python
# BAD — actual error details are lost forever
async def save_to_docs(self, content: str, org_slug: str) -> str:
    try:
        resp = await self.client.post(f"/api/orgs/{org_slug}/pages", json={"content": content})
        resp.raise_for_status()
        return "Saved successfully"
    except httpx.HTTPStatusError:
        return _ERR_SAVE  # "An error occurred while saving" — no logging!
```

**Correct:**
```python
# GOOD — log everything, then return the generic message
async def save_to_docs(self, content: str, org_slug: str) -> str:
    try:
        resp = await self.client.post(f"/api/orgs/{org_slug}/pages", json={"content": content})
        resp.raise_for_status()
        return "Saved successfully"
    except httpx.HTTPStatusError as exc:
        logger.error(
            "save_to_docs failed",
            org_slug=org_slug,
            status_code=exc.response.status_code,
            response_text=exc.response.text[:500],
        )
        return _ERR_SAVE
```

**Prevention:**
1. Before every `return <generic_error>`, add a `logger.error()` with: status code, response body (truncated), and all relevant context variables (org_slug, kb_id, etc.)
2. For `ConnectError` (no `.response` attribute), log the exception message and target URL
3. In code review, search for generic error returns and verify each has a preceding log statement

**Seen in:** `klai-knowledge-mcp` `save_to_docs` tool — returned `_ERR_SAVE` without logging the HTTP status or response body, making it impossible to distinguish a 403 (IDOR) from a 422 (Zod `.strict()`) from a 500.

**See also:** `pitfalls/docs-app.md#platform-docs-app-error-logging`

---

## backend-ruff-catches-refactor-bugs

**Severity:** HIGH

**Trigger:** After removing or consolidating functions/imports during a refactor

Ruff's F821 (undefined name) and F401 (unused import) catch real runtime bugs that would otherwise surface in production. After SPEC-BACKEND-001, ruff caught three real issues: an undefined `zitadel` variable (F821 — would crash at runtime), an unused `PortalOrg` import (F401), and an import sorting break (I001).

**Why it matters:**
When consolidating duplicate auth helpers across files, removing a helper also removes its imports. If the file still uses one of those imports directly (not through the helper), F821 catches the undefined name before it becomes a production `NameError`.

**Prevention:**
1. Run `ruff check` after each refactor step, not only at the end
2. Treat F821 as a blocker — it is always a runtime crash
3. After removing a function, grep for all symbols it imported to verify none are used elsewhere in the file

**Seen in:** SPEC-BACKEND-001 — removing `_get_org` from `billing.py` also removed its `zitadel` import, but `zitadel.get_userinfo()` was still called directly for Moneybird contact creation.

---
