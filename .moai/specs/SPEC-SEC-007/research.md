# Research — SPEC-SEC-007

## F-011 — klai-connector token cache pseudo-LRU

**Source:** `.moai/audit/04-tenant-isolation.md` F-011 (lines 284-292), roadmap section `SEC-007` (lines 192-202).

**Code under review:** `klai-connector/app/middleware/auth.py:17-41`.

The module maintains an in-process TTL cache of Zitadel introspection results keyed by SHA-256 of the Bearer token. The existing `_cache_put` evicts via:

```
if len(_token_cache) >= _CACHE_MAX_SIZE:
    oldest_key = next(iter(_token_cache))
    _token_cache.pop(oldest_key, None)
```

On a plain `dict`, `next(iter(...))` returns the *first-inserted* key — insertion-order, not recency-order. There is no mechanism to mark cache hits as recently used. A steady stream of new tokens therefore evicts the hot ones first, undermining the cache's purpose at pressure.

**Correct LRU pattern.** `collections.OrderedDict` supports `move_to_end(key)` (O(1), promotes a key to the MRU end) and `popitem(last=False)` (O(1), evicts the LRU end). This is the standard CPython idiom for a bounded LRU cache. `functools.lru_cache` is not applicable here because the cache needs per-entry TTL semantics and must be keyed by a value derived at request time rather than by function arguments.

**Severity.** LOW — the issue is a correctness-of-optimization bug, not a security or correctness bug on the critical path. Under normal load the cache behaves acceptably; only under churn does eviction become suboptimal.

## F-015 — background tasks without `set_tenant()` (documentation half)

**Source:** `.moai/audit/04-2-query-inventory.md` F-015 (lines 116-133). Related context in `.moai/audit/04-3-prework-caddy.md` PRE-A (lines 6-38).

**Code under review:**
- `klai-portal/backend/app/services/bot_poller.py` (polling loop around lines 110-121).
- `klai-portal/backend/app/services/invite_scheduler.py` (three `AsyncSessionLocal()` blocks at ~63, ~96, ~119).
- `klai-portal/backend/app/services/connector_credentials.py:165` (`rotate_kek` cross-org DEK sweep).

Each site opens `AsyncSessionLocal()` and runs a query without ever calling `set_tenant()` (no `set_config('app.current_org_id', ..., false)`). The audit marks these as **intentional**: they are scheduler / operator tasks that need to see rows across every tenant.

### The RLS paradox

PRE-A verified that the DB role used by portal-api is `portal_api` with `bypassrls=false`. That means RLS *is* enforced for runtime queries. Yet these background tasks continue to work in production — i.e., they do appear to see cross-org rows. There are three candidate explanations, none confirmed:

1. The RLS policies on the relevant tables (`vexa_meetings`, `portal_orgs`) have clauses like `app.current_org_id IS NULL OR org_id = current_org_id` that fall through to "all rows" when the session variable is unset.
2. The session variable defaults to a value (e.g., empty string) that no RLS policy matches, making the queries return zero rows — and nobody has noticed because the tasks silently do nothing.
3. Some other mechanism (a different role connection, a postgres default, etc.) bypasses RLS for these specific connections.

`.moai/audit/04-3-prework-caddy.md` explicitly flags this as an unresolved parking-lot item requiring a `SELECT tablename, policyname, cmd, qual FROM pg_policies WHERE tablename = 'vexa_meetings'` inspection against production.

### Scope of this SPEC

This SPEC deliberately **does not** attempt to resolve the paradox. It only documents the *current* code-level intent via `@MX:NOTE` + `@MX:REASON` annotations, so that:

- Future agents reading `bot_poller.py` do not delete the `AsyncSessionLocal()` call thinking it is a forgotten `set_tenant()`.
- Future agents adding new user-scoped queries do not copy the pattern and accidentally introduce a cross-tenant leak.
- A future SPEC investigating the RLS paradox has a clean set of flagged sites to reason about.

The annotations explicitly note the paradox instead of claiming the code is known-correct. This is important — silently labelling the code "intentional — safe" would mask a real open question.

## @MX annotation format

Reference: `.claude/rules/moai/workflow/mx-tag-protocol.md`.

- Comment prefix for Python is `#` — so annotations use `# @MX:NOTE:`, `# @MX:REASON:`, `# @MX:SPEC:`.
- `@MX:REASON` is mandatory for `@MX:WARN` and `@MX:ANCHOR` but is explicitly *added* here on `@MX:NOTE` because the intent needs a first-class rationale: "this is intentional, here is why".
- `@MX:SPEC: SPEC-SEC-007` sub-line makes the annotation cohort discoverable via grep for future follow-up (e.g., when the RLS paradox is resolved).
- `code_comments: en` per `.moai/config/sections/language.yaml` — annotations in English.

## Prior art and precedents

- `.claude/rules/klai/projects/portal-backend.md` documents `AsyncSessionLocal()` as the standard pattern for fire-and-forget writes (audit log, analytics) that must survive caller exceptions. That use case is *within a single org*. The F-015 sites differ in that they are *cross-org by design*. The annotations should make that distinction visible.
- SQLAlchemy 2.0 `AsyncSession` has no built-in per-session RLS hook, so `set_tenant()` is the project's conventional escape. Annotating its absence is the only viable documentation mechanism short of a behavioral refactor.

## Why two separate changes, one SPEC

REQ-1 (connector LRU) and REQ-2 (portal annotations) share nothing technically. They are grouped in SEC-007 because the audit roadmap groups them as "P3 / code-quality / rollback-safe" items, and both are small enough that a separate SPEC per item would be process overhead.

Implementation can (and should) split into two commits for independent revertability — see `plan.md` Phases 1 and 2.

## Out of scope — parked items

The following are explicitly *not* addressed here:

- Resolving the F-015 RLS paradox (pg_policies inspection, candidate fixes like dedicated `portal_scheduler` role, explicit `set_config('app.current_org_id', NULL)` per task, or policy `IS NULL OR ...` pattern).
- The other F-009 concerns around klai-connector (long-lived portal bypass secret, non-constant-time compare) — those are SEC-004 scope.
- `hmac.compare_digest` for the portal secret compare — SEC-004.
- Cross-worker cache coordination (Redis) for the connector token cache — not part of SEC-007.
