# Acceptance Criteria — SPEC-SEC-007

EARS format criteria mapping onto the requirements in `spec.md`. All criteria are independently verifiable.

## AC-1: LRU Token Cache (REQ-1)

**AC-1.1 — Eviction policy is true LRU.**
**WHEN** the klai-connector token cache reaches `_CACHE_MAX_SIZE` **AND** a new entry is added via `_cache_put(token_hash, claims)` **THE** cache **SHALL** evict the **least-recently-used** entry (not the least-recently-inserted).

**AC-1.2 — Read marks entry as recently used.**
**WHEN** `_cache_get(token_hash)` returns a non-expired entry **THE** cache **SHALL** move that entry to the most-recently-used position before returning.

**AC-1.3 — Expired entries are removed, not promoted.**
**WHEN** `_cache_get(token_hash)` resolves to an expired entry **THE** cache **SHALL** remove the entry and return `None` **AND** the expired entry **SHALL NOT** be moved to the most-recently-used position (it no longer exists).

**AC-1.4 — Overwrite semantics.**
**WHEN** `_cache_put(token_hash, claims)` is called with an already-present key **THE** cache **SHALL** overwrite the existing entry **AND** mark it as most-recently-used.

**AC-1.5 — Retrieve-then-fill-to-max unit test.**
**GIVEN** the `_token_cache` has been filled to exactly `_CACHE_MAX_SIZE - 1` entries numbered `k_1 .. k_{N-1}` (inserted in ascending order)
**AND** `_cache_get(k_1)` is invoked (making `k_1` most-recently-used)
**AND** a new entry `k_new` is inserted via `_cache_put`, bringing the cache to `_CACHE_MAX_SIZE`
**AND** a second new entry `k_new2` is inserted via `_cache_put`, triggering eviction
**WHEN** the test inspects `_token_cache`
**THEN** the test **SHALL** assert:
- `k_1` is still present (not evicted, because it was recently read)
- `k_2` has been evicted (it is now the least-recently-used)
- `k_new` and `k_new2` are both present
- `len(_token_cache) == _CACHE_MAX_SIZE`

**AC-1.6 — No regression on cache constants.**
**WHEN** the module `klai-connector/app/middleware/auth.py` is imported **THE** constants `_CACHE_MAX_SIZE` **SHALL** equal `1000` **AND** `_CACHE_TTL` **SHALL** equal `300`.

**AC-1.7 — No regression on AuthMiddleware.**
**WHEN** the existing klai-connector auth tests run (Zitadel introspection happy path, 401 path, portal bypass path) **THE** suite **SHALL** pass without modification.

## AC-2: @MX Annotations on Cross-Org Background Tasks (REQ-2)

**AC-2.1 — Every flagged site is annotated.**
**WHERE** a portal background task deliberately skips `set_tenant()` on an `AsyncSessionLocal()` session **THE** code immediately preceding that session **SHALL** carry an `# @MX:NOTE:` comment with an `# @MX:REASON:` sub-line explaining the cross-org intent.

**AC-2.2 — bot_poller.py covered.**
**WHEN** `klai-portal/backend/app/services/bot_poller.py` is inspected **THE** file **SHALL** contain `@MX:NOTE` + `@MX:REASON` annotations directly above both the active-meeting poll query (current line ~112) **AND** the stuck-meeting recovery query (current line ~117).

**AC-2.3 — invite_scheduler.py covered.**
**WHEN** `klai-portal/backend/app/services/invite_scheduler.py` is inspected **THE** file **SHALL** contain `@MX:NOTE` + `@MX:REASON` annotations directly above each of the three `async with AsyncSessionLocal() as db:` blocks (currently lines ~63, ~96, ~119).

**AC-2.4 — connector_credentials.py covered.**
**WHEN** `klai-portal/backend/app/services/connector_credentials.py` is inspected **THE** line 165 `select(PortalOrg).where(PortalOrg.connector_dek_enc.isnot(None))` query inside `rotate_kek` **SHALL** be preceded by an `@MX:NOTE` + `@MX:REASON` pair.

**AC-2.5 — SPEC back-reference.**
**WHEN** any annotation added under this SPEC is inspected **THE** annotation block **SHALL** include `# @MX:SPEC: SPEC-SEC-007`.

**AC-2.6 — Paradox honesty.**
**WHEN** any annotation added under this SPEC is inspected **THE** `@MX:REASON` text **SHALL** state that the bypass is *intentional* and **SHALL NOT** claim the code is known-correct under RLS; it **SHALL** reference the F-015 RLS paradox tracked in `.moai/audit/04-3-prework-caddy.md`.

**AC-2.7 — No stray annotations.**
**WHEN** `klai-portal/backend/app/services/` is grep'd for `@MX:SPEC: SPEC-SEC-007` **THE** results **SHALL** be confined to the three files listed in AC-2.2 through AC-2.4.

**AC-2.8 — Lint and type check.**
**WHEN** `ruff check` and `pyright` are run on both `klai-connector` and `klai-portal/backend` **THE** exit code **SHALL** be `0` with no new warnings introduced by this SPEC.

## AC-3: Cross-cutting

**AC-3.1 — Rollback-safe.**
**WHEN** the commit(s) implementing this SPEC are reverted via `git revert` **THE** system **SHALL** return to the pre-SPEC state without migration work.

**AC-3.2 — No behavioral change.**
**WHEN** integration traffic runs against klai-connector **THE** observable HTTP responses (status codes, bodies, headers, timing within normal variance) **SHALL** remain unchanged relative to the pre-SPEC baseline.
