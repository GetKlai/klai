---
id: SPEC-SEC-007
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: low
---

# SPEC-SEC-007: Code Quality & Annotations

## HISTORY

### v0.1.0 (2026-04-19)
- Initial draft. Combines F-011 (klai-connector token cache insertion-order pseudo-LRU) and the documentation portion of F-015 (portal background tasks that intentionally skip `set_tenant()` for cross-org system operations).
- Scope strictly limited to rollback-safe, non-behavioral corrections plus @MX annotations. Does **not** attempt to resolve the open F-015 RLS paradox — that stays in the audit parking lot.

---

## Goal

Fix two P3 code-quality items from the Fase 3 security audit:

1. **Correct LRU semantics in the klai-connector token introspection cache** (F-011). Today the cache evicts by insertion-order (`next(iter(_token_cache))`) even though the cache is described as "LRU-like". Replace it with a real LRU implemented via `collections.OrderedDict` so the hottest tokens stay cached under pressure.
2. **Annotate the three portal background tasks that intentionally bypass tenant scoping** (F-015, documentation half). Today `bot_poller.py`, `invite_scheduler.py`, and `connector_credentials.rotate_kek` open an `AsyncSessionLocal()` and query cross-org without ever calling `set_tenant()`. Future agents reading this code cannot tell whether this is a bug or a deliberate system-task escape hatch. Add `@MX:NOTE` + `@MX:REASON` annotations so the intent is explicit.

Both changes are rollback-safe refactors with zero behavioral impact on production traffic.

## Success Criteria

- The klai-connector token cache uses true LRU semantics: on a cache hit the entry is marked "recently used", and on overflow the **least-recently-used** entry is evicted — not an arbitrary insertion-order entry.
- A retrieve-then-fill-to-max unit test demonstrates the retrieved entry survives, while the least-recently-used entry is evicted.
- Every portal background query that deliberately runs without `set_tenant()` carries an `@MX:NOTE` plus `@MX:REASON` annotation clearly stating the cross-org intent and (where relevant) the SPEC-SEC-007 back-reference.
- `bot_poller.py` lines 111-121, `invite_scheduler.py` lines 63, 96, and 119 `AsyncSessionLocal()` blocks, and `connector_credentials.py` line 165 are all annotated.
- Grep for `AsyncSessionLocal()` in `klai-portal/backend/app/services/` surfaces annotations on every intentional cross-org usage.
- No functional change on the connector auth path: existing integration tests (Zitadel introspection happy path + 401 path + portal bypass path) still pass.
- `ruff check` and `pyright` remain green on both services.

---

## Environment

- **klai-connector:** Python 3.13, FastAPI, `starlette.middleware.base.BaseHTTPMiddleware`, httpx, uv. Single-process per worker (Gunicorn/Uvicorn), in-process `_token_cache` module global.
- **klai-portal backend:** Python 3.13, FastAPI, SQLAlchemy 2.0 async, `AsyncSessionLocal`, PostgreSQL with RLS via `app.current_org_id`. `portal_api` DB role has `bypassrls=false` (confirmed by PRE-A in `.moai/audit/04-3-prework-caddy.md`).
- Background task runners invoked via APScheduler / long-running asyncio tasks inside the `portal-api` process.

## Assumptions

- The klai-connector cache has a soft max of 1000 entries and a 5-minute TTL; those constants stay unchanged.
- `collections.OrderedDict` on CPython 3.13 preserves insertion order and supports `move_to_end` / `popitem(last=False)` in O(1) — acceptable for the existing 1000-entry ceiling.
- The intent of the three flagged portal background code paths is genuinely cross-org. This SPEC documents the **current** intent only and does **not** validate whether the queries actually return rows under strict RLS. That behavioral question is tracked separately as the "F-015 RLS paradox" parking-lot item in `.moai/audit/04-3-prework-caddy.md` (`portal_api` has `bypassrls=false`, yet these tasks work today — the root cause of why is unresolved).
- `@MX:NOTE` + `@MX:REASON` annotations follow the conventions in `.claude/rules/moai/workflow/mx-tag-protocol.md`; `code_comments: en` applies (per `.moai/config/sections/language.yaml`).

## Out of Scope

- **Does NOT fix F-015 behaviorally.** Whether these background tasks actually see cross-org rows under strict RLS is an open question — the "RLS paradox" — and is explicitly parked in `.moai/audit/04-3-prework-caddy.md`. This SPEC only adds *code-level documentation* of the current intent. A future SPEC will decide whether to introduce a dedicated DB role, policy `IS NULL OR org_id = X` pattern, or explicit `set_config('app.current_org_id', NULL)` per task.
- Does **not** add cross-worker/multi-process cache coordination (e.g., Redis) for the klai-connector token cache. Per-worker in-process cache remains by design.
- Does **not** change the cache size (`_CACHE_MAX_SIZE = 1000`) or TTL (`_CACHE_TTL = 300`).
- Does **not** touch the other F-009 connector concerns (portal bypass secret, `hmac.compare_digest`) — those are SEC-004 scope.
- Does **not** re-annotate tag types other than `@MX:NOTE` / `@MX:REASON`; no ANCHOR/WARN/TODO changes are triggered by this SPEC.
- Does **not** introduce new tests beyond the LRU eviction unit test. No coverage targets raised.

---

## Security Findings Addressed

- **F-011 (LOW, klai-connector):** Insertion-order eviction in `_cache_put` at `klai-connector/app/middleware/auth.py:37-41`. Source: `.moai/audit/04-tenant-isolation.md` F-011 section (lines 284-292).
- **F-015 (MEDIUM, documentation half only):** Portal background tasks run without `set_tenant()`:
  - `klai-portal/backend/app/services/bot_poller.py` around line 111 (active VexaMeeting polling) and line 117 (stuck-meeting recovery sweep).
  - `klai-portal/backend/app/services/invite_scheduler.py` at lines 63, 96, 119 (three `AsyncSessionLocal()` blocks for cross-org iCal dedupe).
  - `klai-portal/backend/app/services/connector_credentials.py` at line 165 (KEK rotation across all orgs).
  - Source: `.moai/audit/04-2-query-inventory.md` F-015 section (lines 116-133), plus the unresolved "RLS paradox" noted in `.moai/audit/04-3-prework-caddy.md` PRE-A (lines 26-38).

Roadmap entry: `.moai/audit/99-fix-roadmap.md` section "SEC-007 — Code-quality / correctness [P3]" (lines 192-202).

---

## Requirements

### REQ-1: LRU Token Cache in klai-connector

**REQ-1.1:** The `_token_cache` in `klai-connector/app/middleware/auth.py` SHALL be declared as `collections.OrderedDict[str, tuple[dict[str, Any], float]]` instead of a plain `dict`.

**REQ-1.2:** WHEN `_cache_get(token_hash)` resolves to a non-expired entry, the function SHALL call `_token_cache.move_to_end(token_hash)` to mark the entry as most-recently-used before returning it.

**REQ-1.3:** WHEN `_cache_get(token_hash)` resolves to an expired entry, the function SHALL remove the entry via `_token_cache.pop(token_hash, None)` (unchanged behavior) and return `None`.

**REQ-1.4:** WHEN `_cache_put(token_hash, claims)` is called AND `len(_token_cache) >= _CACHE_MAX_SIZE`, the function SHALL evict the least-recently-used entry via `_token_cache.popitem(last=False)`.

**REQ-1.5:** `_cache_put` SHALL insert or overwrite the entry at the most-recently-used end of the ordered map (plain `_token_cache[token_hash] = (...)` on an `OrderedDict` inserts at the tail when the key is new; for an existing key, `move_to_end` SHALL be called before the assignment).

**REQ-1.6:** The cache constants `_CACHE_MAX_SIZE = 1000` and `_CACHE_TTL = 300` SHALL remain unchanged.

**REQ-1.7:** Public behavior of the `AuthMiddleware.dispatch` method SHALL remain unchanged. The cache contract (key = SHA-256 hash of Bearer token, value = `(claims, expiry_monotonic)`) SHALL remain unchanged.

**REQ-1.8:** A unit test under `klai-connector/tests/` SHALL verify: (a) filling the cache to `_CACHE_MAX_SIZE`, (b) performing a `_cache_get` on the oldest inserted key so it becomes most-recently-used, (c) inserting one more entry via `_cache_put`, (d) asserting the **second-oldest** key was evicted (not the key that was just read), (e) asserting the retrieved key is still present.

### REQ-2: @MX Annotations on Cross-Org Background Tasks

**REQ-2.1:** `klai-portal/backend/app/services/bot_poller.py` SHALL carry an `@MX:NOTE` comment immediately above the `AsyncSessionLocal()` block at line 111 (the `select(VexaMeeting).where(VexaMeeting.status.in_(ACTIVE_STATUSES))` polling query) AND above the follow-up `stuck-meeting` sweep query at line 117. Both annotations SHALL include an `@MX:REASON` sub-line explaining that the task polls Vexa-initiated meetings across **all** orgs as a system-level scheduler function, and that `set_tenant()` is deliberately omitted.

**REQ-2.2:** `klai-portal/backend/app/services/invite_scheduler.py` SHALL carry an `@MX:NOTE` comment immediately above each of the three `async with AsyncSessionLocal() as db:` blocks currently at lines 63, 96, and 119. Each annotation SHALL include an `@MX:REASON` sub-line explaining that iCal UID deduplication must scan across all orgs (UIDs are globally unique across tenants) and that `set_tenant()` is therefore intentionally omitted.

**REQ-2.3:** `klai-portal/backend/app/services/connector_credentials.py` SHALL carry an `@MX:NOTE` comment immediately above the `select(PortalOrg).where(PortalOrg.connector_dek_enc.isnot(None))` query at line 165 inside `rotate_kek`. The annotation SHALL include an `@MX:REASON` sub-line explaining that KEK rotation is an operator-invoked maintenance task that must re-encrypt every org's DEK in a single pass and therefore runs without `set_tenant()`.

**REQ-2.4:** Every `@MX:NOTE` added under this SPEC SHALL reference `SPEC-SEC-007` via an `@MX:SPEC: SPEC-SEC-007` sub-line so the annotations stay discoverable as a cohort.

**REQ-2.5:** Each annotation SHALL explicitly state that the **behavioral** question of how these queries return rows under strict RLS (`portal_api.bypassrls=false`) is tracked separately as the F-015 RLS paradox in `.moai/audit/04-3-prework-caddy.md`. The annotations SHALL NOT claim the code is "correct" — only that the omission of `set_tenant()` is **intentional**.

**REQ-2.6:** Annotations SHALL follow the `# @MX:TAG:` Python comment syntax per `.claude/rules/moai/workflow/mx-tag-protocol.md`, with `code_comments: en`.

**REQ-2.7:** No other file in `klai-portal/backend/app/services/` SHALL be annotated under this SPEC. If additional `AsyncSessionLocal()` sites without `set_tenant()` are discovered during implementation, they SHALL be reported in the completion summary and deferred to a follow-up SPEC — not annotated opportunistically.

---

## Non-Functional Requirements

- **Behavior preservation:** REQ-1 SHALL NOT change any observable auth outcome (same HTTP status codes, same claim payloads, same timing bounds within normal variance).
- **Rollback safety:** Both REQ-1 and REQ-2 SHALL be individually revertible via `git revert` without cascading changes.
- **Documentation quality:** `@MX:REASON` text SHALL be precise enough that a future agent without audit context can decide whether a new cross-org query should follow the same pattern or must instead call `set_tenant()`.
- **No scope creep:** This SPEC SHALL NOT introduce a new DB role, new RLS policy, new cross-worker cache, or new observability metric. Any such change belongs in a follow-up SPEC.
