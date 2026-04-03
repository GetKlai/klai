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
| [backend-sqlalchemy-returning-rls](#backend-sqlalchemy-returning-rls) | CRIT | SQLAlchemy ORM adds implicit RETURNING to all inserts — breaks RLS tables with separate SELECT/INSERT policies |
| [backend-request-session-rollback-loses-writes](#backend-request-session-rollback-loses-writes) | HIGH | Fire-and-forget writes (audit, analytics) on the request session are lost when caller raises an exception |
| [backend-api-status-rename-blast-radius](#backend-api-status-rename-blast-radius) | HIGH | Status string values are cross-layer contracts — grep entire codebase before renaming |
| [backend-event-name-must-match-action](#backend-event-name-must-match-action) | HIGH | Event name must match the actual user action, not the configuration step |
| [backend-event-field-availability](#backend-event-field-availability) | HIGH | Verify emit context has the fields your dashboard query needs |

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

## backend-sqlalchemy-returning-rls

**Severity:** CRIT

**Trigger:** Using SQLAlchemy ORM inserts on a table with PostgreSQL Row-Level Security (RLS) policies

SQLAlchemy ORM adds an implicit `RETURNING primary_key` clause to ALL insert statements — including `insert(Model).values()` and `insert(Model.__table__).values()`. PostgreSQL evaluates SELECT RLS policies on the `RETURNING` clause. If the inserting role (e.g. a service account) is not permitted by the SELECT policy, the insert fails with a permission error even though the INSERT policy allows it.

**Why it happens:**
SQLAlchemy's ORM layer needs the returned primary key to populate the mapped object's identity. There is no ORM-level option to suppress `RETURNING`. PostgreSQL treats `RETURNING` as a read operation, so it checks SELECT policies — not just INSERT policies.

**Prevention:**
1. If RLS SELECT and INSERT have different policy conditions, split the `ALL` policy into separate `SELECT` and `INSERT` policies with appropriate conditions for each
2. If the inserting role should never read back rows (e.g. audit logging), use `text()` raw SQL to bypass ORM's implicit `RETURNING`:
   ```python
   await session.execute(
       text("""
           INSERT INTO my_table (col1, col2)
           VALUES (:val1, CAST(:val2 AS jsonb))
       """),
       {"val1": value1, "val2": json.dumps(value2)},
   )
   ```
3. Note: `::jsonb` type casts conflict with SQLAlchemy's `:param` syntax — always use `CAST(:param AS jsonb)` instead
4. When adding RLS to a table, audit all insert paths for implicit `RETURNING` by checking whether they use ORM models

**Seen in:** `portal_audit_log` table — RLS `ALL` policy required `auth.uid() = user_id` for SELECT, but audit inserts used a service role. All three ORM approaches (`session.add()`, `insert(Model).values()`, `insert(Model.__table__).values()`) failed. Only `text()` raw SQL worked.

---

## backend-request-session-rollback-loses-writes

**Severity:** HIGH

**Trigger:** Writing audit logs, analytics, or other fire-and-forget records using the request-scoped database session, when the caller may raise an exception afterward

If the endpoint raises `HTTPException` (or any exception) after writing an audit log entry, the request-scoped session rolls back the entire transaction — including the audit entry. SAVEPOINTs do not help because the outer transaction rollback discards all savepoints.

**Why it happens:**
FastAPI middleware (or dependency cleanup) rolls back the session on any unhandled exception. The audit write and the business logic share the same session and transaction. Even `begin_nested()` (SAVEPOINT) is lost when the outer transaction rolls back.

**Prevention:**
1. Use an independent session (`AsyncSessionLocal()`) for writes that must survive caller exceptions:
   ```python
   async def log_event(action: str, user_id: str, details: dict) -> None:
       async with AsyncSessionLocal() as session:
           await session.execute(text("INSERT INTO audit_log ..."), params)
           await session.commit()
   ```
2. The independent session opens and commits its own transaction — completely decoupled from the request lifecycle
3. Wrap in try/except so audit failures never crash the business endpoint
4. This pattern is appropriate for audit logs, analytics, and any write that is observational rather than transactional

**Seen in:** `app/services/audit.py` — audit entries for failed login attempts, permission denials, and other error paths were silently lost because the caller raised `HTTPException` after `log_event()`, rolling back the shared session.

---

## backend-api-status-rename-blast-radius

**Severity:** HIGH

**Trigger:** Renaming status string values in an API response or database column

Status string values (e.g., `"recording"`, `"processing"`, `"completed"`) are cross-layer contracts. The same string is hardcoded in: backend enum/constants, database queries, API response schemas, frontend polling logic, UI badge components, i18n translation keys, and sometimes external webhook consumers.

**What happened:** SPEC-VEXA-001 renamed Vexa meeting statuses during the agentic-runtime migration. The backend was updated, but the frontend badges, polling intervals, and i18n keys still referenced the old values — requiring multiple follow-up fix commits.

**Rule:** Before renaming any status value:
1. `grep -r "old_value"` across the entire monorepo (backend, frontend, configs, tests, i18n)
2. Check all case variants: `old_value`, `OLD_VALUE`, `OldValue`, `old-value`
3. Update all consumers in a single commit or coordinated PR
4. If the status is exposed via API, consider supporting both old and new values during a transition period

**See also:** `process-search-all-case-variants`, `process-convention-change-blast-radius`

---

## backend-event-name-must-match-action

**Severity:** HIGH

**Trigger:** Placing an `emit_event()` call on an endpoint and choosing the event name

The event name must describe the actual user action that the endpoint performs, not a configuration step that precedes it. Placing an event on the wrong endpoint means your analytics measure configuration activity instead of actual usage.

**Why it happens:**
When a feature has multiple steps (configure → trigger → complete), the emit call is placed on whichever endpoint the developer looks at first. The configuration endpoint is often the most obvious entry point, but it does not represent the action the event name describes.

**What happened:**
`knowledge.uploaded` was initially placed on `create_connector` (which only saves a connector configuration). The actual document fetching happens in `trigger_sync`. The event name says "uploaded" but measured "configured" — completely different metrics.

**Prevention:**
1. Before placing `emit_event()`, write down in one sentence what the event name means in plain language
2. Verify the endpoint actually performs that action — not a preceding step
3. If the feature has a multi-step flow (configure → trigger → complete), trace which endpoint does the action the event name describes
4. Review: "If a user configures but never triggers, should this event fire?" — if no, you are on the wrong endpoint

**Seen in:** SPEC-GRAFANA-METRICS — `knowledge.uploaded` moved from `create_connector` to `trigger_sync` during self-review.

---

## backend-event-field-availability

**Severity:** HIGH

**Trigger:** Writing a Grafana/analytics query that uses `COUNT(DISTINCT field)` or filters on a field from `emit_event()` metadata

Before writing a dashboard query that depends on a specific field (e.g., `org_id`, `user_id`), verify that the field is actually populated in the emit context. Events emitted before authentication (login, signup) or from background tasks may not have access to session-scoped fields like `org_id`.

**Why it happens:**
Dashboard authors assume all events have the same metadata fields. But event emission happens at different points in the request lifecycle — pre-auth endpoints (login) have no org context, while authenticated endpoints (meetings, connectors) do.

**What happened:**
A Feature Adoption panel used `COUNT(DISTINCT org_id) WHERE event_type = 'login'` to represent chat adoption. Login events are emitted in `auth.py` before org resolution — `org_id` is always NULL. The panel permanently showed 0.

**Prevention:**
1. Before writing a `COUNT(DISTINCT field)` query, check the `emit_event()` call site and verify the field is passed
2. Pre-auth events (`login`, `signup.started`) will not have `org_id` — do not use org-based aggregation on them
3. If a dashboard panel needs a field that the event does not carry, either enrich the event or choose a different event
4. Document which fields each event type guarantees in a central event catalog

**Seen in:** SPEC-GRAFANA-METRICS — Feature Adoption panel removed "Chat" metric because login events lack org_id.

---
