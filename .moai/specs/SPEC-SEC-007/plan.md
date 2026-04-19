# Implementation Plan — SPEC-SEC-007

Two independent, rollback-safe changesets. Recommend landing as two separate commits (or one PR with two commits) so REQ-1 and REQ-2 can be reverted independently.

## Phase 1 — Connector LRU cache (REQ-1)

### Target file
- `klai-connector/app/middleware/auth.py` (lines 17-41 as of 2026-04-19)

### Changes

1. **Import change.** Add `from collections import OrderedDict` at the top of the file.

2. **Declaration change (line 18).** Replace:
   ```
   _token_cache: dict[str, tuple[dict[str, Any], float]] = {}
   ```
   with:
   ```
   _token_cache: "OrderedDict[str, tuple[dict[str, Any], float]]" = OrderedDict()
   ```

3. **`_cache_get` (lines 23-32).** On a hit that has not expired, call `_token_cache.move_to_end(token_hash)` before returning the claims. On an expired hit, keep the existing `_token_cache.pop(token_hash, None)` and return `None`.

4. **`_cache_put` (lines 35-41).** Replace the insertion-order eviction:
   ```
   if len(_token_cache) >= _CACHE_MAX_SIZE:
       oldest_key = next(iter(_token_cache))
       _token_cache.pop(oldest_key, None)
   _token_cache[token_hash] = (claims, time.monotonic() + _CACHE_TTL)
   ```
   with the LRU variant:
   ```
   if token_hash in _token_cache:
       _token_cache.move_to_end(token_hash)
   elif len(_token_cache) >= _CACHE_MAX_SIZE:
       _token_cache.popitem(last=False)
   _token_cache[token_hash] = (claims, time.monotonic() + _CACHE_TTL)
   ```
   This handles three cases: (a) overwriting an existing key promotes it; (b) inserting a new key when full evicts the LRU entry; (c) inserting a new key when not full appends at the MRU end.

5. **Keep constants unchanged.** `_CACHE_MAX_SIZE = 1000`, `_CACHE_TTL = 300`. Keep the docstrings and `AuthMiddleware.dispatch` untouched.

### Tests

Add a new test module (or append to the existing middleware tests) under `klai-connector/tests/` exercising:

- `test_lru_evicts_least_recently_used` — fill `_CACHE_MAX_SIZE`, read a specific key, insert one more, assert the read key survives while the next-oldest key is evicted. Covers AC-1.1, AC-1.2, AC-1.5.
- `test_expired_entry_is_purged_on_get` — seed an entry with expiry in the past via `time.monotonic()` mock, assert `_cache_get` returns `None` and the key is gone. Covers AC-1.3.
- `test_put_existing_key_promotes` — put key `k`, put another key, put `k` again, insert up to max, assert `k` survives the next eviction. Covers AC-1.4.

Use a module-level `pytest` fixture that clears `_token_cache` between tests (the module global survives between test functions otherwise).

### Verification

- `cd klai-connector && uv run pytest tests/ -k cache` — new tests green, existing tests untouched.
- `cd klai-connector && uv run ruff check .` — clean.
- `cd klai-connector && uv run --with pyright pyright` — clean.

## Phase 2 — Portal @MX annotations (REQ-2)

Target files and insertion points (line numbers as of 2026-04-19 — implementer verifies before editing):

### 2.1 — `klai-portal/backend/app/services/bot_poller.py`

Before line 111 (inside the polling loop, `async with AsyncSessionLocal() as db:` followed by `select(VexaMeeting).where(VexaMeeting.status.in_(ACTIVE_STATUSES))`), insert:

```
# @MX:NOTE: Cross-org system task. This poll runs without set_tenant() so it
# can see active Vexa meetings across every tenant in one pass.
# @MX:REASON: The Vexa meeting scheduler is a platform-level process, not a
# user request. There is no single org_id to bind. The omission of set_tenant()
# is intentional; whether strict RLS under portal_api.bypassrls=false permits
# this query at all is the unresolved F-015 "RLS paradox" tracked in
# .moai/audit/04-3-prework-caddy.md. Do not copy this pattern for user-
# scoped work — use set_tenant() there.
# @MX:SPEC: SPEC-SEC-007
```

Repeat the same annotation block (adapted for the "stuck meeting" sweep — reference stuck-meeting recovery rather than active polling in the REASON) before line 117-118 where the `select(VexaMeeting).where(VexaMeeting.status == "stopping", ...)` query starts.

### 2.2 — `klai-portal/backend/app/services/invite_scheduler.py`

Before each of the three `async with AsyncSessionLocal() as db:` blocks at lines ~63, ~96, ~119, insert:

```
# @MX:NOTE: Cross-org system task. iCal UID dedupe must scan all tenants
# because iCal UIDs are globally unique across orgs.
# @MX:REASON: Skipping set_tenant() is intentional — there is no single org
# context for a scheduler that reconciles external calendar state.
# Behavior under strict RLS (portal_api.bypassrls=false) is the unresolved
# F-015 "RLS paradox" tracked in .moai/audit/04-3-prework-caddy.md.
# @MX:SPEC: SPEC-SEC-007
```

Tailor the first sentence of each `@MX:REASON` per call-site if the purpose differs (e.g., "scheduled lookup", "completion cleanup", "registry rebuild") — verify by reading surrounding code before annotating.

### 2.3 — `klai-portal/backend/app/services/connector_credentials.py`

Before line 165, inside `ConnectorCredentialStore.rotate_kek`, before `result = await db.execute(select(PortalOrg).where(PortalOrg.connector_dek_enc.isnot(None)))`, insert:

```
# @MX:NOTE: Cross-org system task. KEK rotation re-encrypts every org's DEK
# in a single administrative pass. Receives the DB session from the caller;
# set_tenant() is deliberately not called here.
# @MX:REASON: This function is invoked only by operator-initiated key
# rotation, never from a user request. Scoping to a single org would defeat
# the purpose. Under strict RLS (portal_api.bypassrls=false) this sweep's
# ability to see all rows depends on the unresolved F-015 "RLS paradox"
# tracked in .moai/audit/04-3-prework-caddy.md.
# @MX:SPEC: SPEC-SEC-007
```

### 2.4 — Follow-up discovery

While editing, grep `klai-portal/backend/app/services/` for `AsyncSessionLocal()` and for each hit verify whether `set_tenant()` is called within the block. If a hit is found that **is not** in the three files above and **does not** call `set_tenant()`, **stop**, do not annotate it, and include it in the completion report so a follow-up SPEC can triage it.

### Verification

- `cd klai-portal/backend && uv run ruff check .` — clean (comments cannot break ruff, but verify).
- `cd klai-portal/backend && uv run --with pyright pyright` — clean.
- Existing portal test suite unchanged: `uv run pytest` — green baseline.

## Phase 3 — Completion report

After both phases land, the agent SHALL produce a short markdown report listing:

- Files changed with line counts.
- Any newly discovered `AsyncSessionLocal()` cross-org sites found during grep (AC-2.7 / Phase 2.4) and explicitly flagged as out-of-scope for follow-up.
- Test output snippets confirming AC-1 cases.
- Confirmation that `ruff` and `pyright` exit zero on both services.

## Dependencies & ordering

- REQ-1 (connector LRU) and REQ-2 (portal annotations) are **independent**. They can land in either order or in parallel PRs.
- Neither change requires a migration, a config change, or a coordinated deploy. Each service can be rolled out independently.

## Rollback

- `git revert <commit>` for either phase returns the code to the pre-SPEC state. No data migration is involved.
