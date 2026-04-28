# SPEC-SEC-IDENTITY-ASSERT-001 Progress

## Phase A — Foundation (this branch)

- Started: 2026-04-25
- Branch: `feature/SPEC-SEC-IDENTITY-ASSERT-001` (from `origin/main`)
- Worktree: `~/.moai/worktrees/klai/SPEC-SEC-IDENTITY-ASSERT-001`

### Plan-phase architectural decisions

1. **End-user JWT header (REQ-2.1 design choice)**: `Authorization: Bearer <user_jwt>`.
   Mirrors `klai-retrieval-api/retrieval_api/middleware/auth.py:266-327` which
   already accepts both `X-Internal-Secret` (service auth) and
   `Authorization: Bearer <jwt>` (end-user JWT) on the same route. RFC-standard,
   no conflict in `klai-knowledge-mcp/main.py` (which doesn't currently read
   `Authorization`).
2. **REQ-4 split-vs-global verify**: REQ-4.2 (global verify) for now;
   `/admin/retrieve` mount point is YAGNI until an admin/diagnostic caller
   exists. REQ-4.5 forbids the "admin role on internal-secret" hybrid.
3. **`notebook_visibility` storage**: Qdrant payload field
   (`notebook_visibility` + `owner_user_id`) written at ingest time. Mirrors
   `Notebook.scope` ∈ {"personal", "org"} — no translation layer.

### Delivered

| REQ | Status | Tests | Notes |
|---|---|---|---|
| REQ-1 | ✅ | 22 | `/internal/identity/verify` endpoint; service + Redis cache (REQ-1.5 strict, evidence in key) + structlog |
| REQ-5 | ✅ | 17 | `_notebook_filter` + ingest payload + retrieval guard + backfill script |
| REQ-7 | ✅ | 39 | `klai-libs/identity-assert/` shared library |
| Contract | ✅ | 5 | End-to-end library↔endpoint via in-process ASGI; allowlist drift guard |

Total: **83 tests passing** for SPEC-SEC-IDENTITY-ASSERT-001 in this branch.

### Cleanup pass (2026-04-27)

After the initial Phase A landing, a self-review uncovered:

- **Cache-key looseness**: portal cache keyed on `(caller_service, user_id, org_id)`
  without the SPEC-mandated `evidence` dimension. Fixed: cache key now
  follows REQ-1.5 strictly. Two new tests verify a JWT-evidence cache
  entry does NOT serve a membership-evidence lookup (and vice versa) so
  the `evidence` field in each response honestly reflects what was
  verified.
- **`PyJWKClient` typing punt**: the JWKS resolver was annotated `-> "object"`
  with a local `from jwt import PyJWKClient` and a `# type: ignore[arg-type]`
  at the call site. Cleaned: top-level import, proper `PyJWKClient` type
  annotation, `JwksResolver` protocol made `runtime_checkable`.
- **Missing end-to-end contract test**: the library and endpoint tests
  used independent mocks. A new `test_identity_verify_contract.py` runs
  the real library against the real endpoint via `httpx.ASGITransport`.
  The test caught a real bug — the library sent `X-Internal-Secret` but
  portal-api's `/internal/*` surface expects `Authorization: Bearer
  <secret>`. Library fixed; tests updated to assert the correct header.
- **`# noqa: S107` cleanup**: replaced inline ignores with a per-file
  rule in `tool.ruff.lint.per-file-ignores` for `tests/*`.
- **Backfill script**: `klai-focus/research-api/scripts/backfill_notebook_visibility.py`
  added — idempotent, dry-run/execute modes, uses Qdrant `IsEmptyCondition`
  to skip already-backfilled chunks.

### Out of scope for this branch (deferred to Phase B / C / D)

- REQ-2 (knowledge-mcp): consume `klai-libs/identity-assert/`, drop
  caller-asserted header forwarding. Separate `/moai run` invocation.
- REQ-3 (scribe): derive `org_id` from JWT `resourceowner` + verify.
- REQ-4 (retrieval-api internal-secret): apply `verify_body_identity` to
  internal-secret callers, require `X-Caller-Service` header.
- REQ-6 (`emit_event`): switch to `request.state.auth` after REQ-4 lands.
- REQ-7.4: editable installs of `klai-libs/identity-assert/` in
  knowledge-mcp / scribe / retrieval-api `pyproject.toml` (Phase B work).

### Migration sequence (per spec.md Risks & Mitigations)

1. ✅ Phase A (`feature/SPEC-SEC-IDENTITY-ASSERT-001`): REQ-1 + REQ-7 + REQ-5
2. ✅ Phase B (`feature/SPEC-SEC-IDENTITY-ASSERT-001-phase-b`): REQ-2
   (knowledge-mcp) + REQ-2.6 endpoint+library extension
3. ✅ Phase C (`feature/SPEC-SEC-IDENTITY-ASSERT-001-phase-c`): REQ-3
   (scribe) — JWT-derived org_id, no portal verify call
4. ✅ Phase D (`feature/SPEC-SEC-IDENTITY-ASSERT-001-phase-d`): REQ-4 +
   REQ-6 (retrieval-api internal-secret callers re-verified against
   portal-api; emit_event sources tenant from `request.state.verified_caller`)

`IDENTITY_VERIFY_MODE` rollback flag was reconsidered and dropped in
Phase B — the library already fails closed on portal outage and
`git revert` is the standard rollback for every other SEC SPEC. Adding
a flag would have shipped the spoof primitive as a configurable option,
which is exactly the thing this SPEC closes. See Phase B sparring for
the reasoning.

---

## Phase B — knowledge-mcp consumer migration

- Started: 2026-04-27
- Branch: `feature/SPEC-SEC-IDENTITY-ASSERT-001-phase-b` (branched from
  Phase A tip; rebases cleanly on main once Phase A merges)
- Worktree: `C:/Users/markv/stack/02 - Voys/Code/klai-identity-assert-phase-b`

### Phase B architectural decisions

1. **REQ-2.6 `org_slug` strategy**: extend Phase A's
   `/internal/identity/verify` to accept optional `claimed_org_slug` and
   always return canonical `org_slug`. Cleanest single round-trip;
   symmetric for future consumers (scribe REQ-3, retrieval-api REQ-4).
   Touches Phase A code but Phase A is on the same unmerged branch
   family — additive, no breaking change.
2. **No feature flag** (`IDENTITY_VERIFY_MODE`): see migration sequence
   note above.
3. **No JWT-refresh retry**: REQ-2.5's "expired-JWT → retry with
   `bearer_jwt=None`" was dropped. A `bearer_jwt=None` membership-only
   verify is *weaker* security than a JWT one — using it as a fallback
   would weaken the spoof closure to dodge an availability issue that
   doesn't actually belong here. Token refresh races are a LibreChat
   responsibility (refresh proactively, or catch deny + refresh + retry
   client-side). Tracked separately under `klai-librechat-patch`.
4. **`Authorization: Bearer <user_jwt>` for incoming end-user JWT**: the
   MCP reads the end-user Zitadel JWT from the same RFC-standard header.
   `X-Internal-Secret` (incoming, knowledge-mcp's own service auth) is a
   different header on a different layer — no conflict.

### Delivered

| Component | Change | Tests |
|---|---|---|
| `klai-portal/backend/app/services/identity_verifier.py` | `verify_identity_claim` accepts `claimed_org_slug`, returns canonical `org_slug` in `VerifyDecision`. New deny code `org_slug_mismatch`. JWT path resolves slug via `_resolve_org_slug`; membership path via combined `_resolve_active_membership_org_slug`. | +6 verifier tests |
| `klai-portal/backend/app/services/identity_verify_cache.py` | Cached payload carries `org_slug`. Cache key unchanged. | (existing tests adapted) |
| `klai-portal/backend/app/api/internal.py` | `IdentityVerifyRequest.claimed_org_slug` (optional), `IdentityVerifySuccess.org_slug` (always returned). Cache-hit slug-mismatch returns 403 without DB round trip. | +4 endpoint tests |
| `klai-libs/identity-assert/klai_identity_assert/models.py` | `VerifyResult.org_slug` field; `org_slug_mismatch` reason code. | +1 model test |
| `klai-libs/identity-assert/klai_identity_assert/client.py` | `verify(claimed_org_slug=...)` parameter; cache-hit slug-mismatch returns deny without portal round trip. | +2 client tests |
| `klai-libs/identity-assert/klai_identity_assert/cache.py` | Re-emits `org_slug` on cache-hit reconstructed `VerifyResult`. | (covered by existing) |
| `klai-knowledge-mcp/main.py` | Module-level `IdentityAsserter` singleton. Renamed `_get_identity` → `_get_claimed_identity` (REQ-2.4). Removed `DEFAULT_ORG_SLUG` fallback (REQ-2.6). All three tools call `_verify_identity` before any upstream call; downstream calls use *verified* identity. `Authorization: Bearer` for incoming end-user JWT. | +8 identity tests |
| `klai-knowledge-mcp/pyproject.toml` | Editable install of `klai-libs/identity-assert/`; dev extras for pytest. | n/a |
| `deploy/docker-compose.yml` | `klai-knowledge-mcp` service: added `PORTAL_API_URL` and `PORTAL_INTERNAL_SECRET` env vars (latter from existing `PORTAL_API_INTERNAL_SECRET` SOPS entry). | n/a |

Total tests added in Phase B: **21 new + adaptations to existing**.

After Phase B (cumulative across both phases):
- Library: 43 tests passing
- Portal-api identity surface: 37 tests passing
- Knowledge-mcp: 17 identity+security tests passing

### Pre-existing breakage NOT caused by Phase B

`klai-knowledge-mcp/tests/test_assertion_mode_taxonomy.py` (11 tests) is
red on the branch tip and remains red after Phase B. The file tests an
unimplemented 6-value assertion-mode taxonomy from SPEC-TAXONOMY-001 RED
phase that was never adopted in `main.py` (which has 5 values:
`factual`/`procedural`/`quoted`/`belief`/`hypothesis`). Cleaning these
tests up belongs to a SPEC-TAXONOMY-001 follow-up, not this SPEC.

### Follow-up tracked outside Phase B

- **LibreChat JWT-refresh proactivity** (replaces the dropped REQ-2.5
  retry): `klai-librechat-patch` should refresh tokens before they
  expire and catch portal `invalid_jwt` deny + refresh + retry on the
  LibreChat side. Without this, expired-JWT races during a token
  refresh window become user-visible "try again" errors. Acceptable
  pre-prod, low-priority post-prod.
- **klai-docs `requireAuthOrService`** (`klai-docs/lib/auth.ts:66-85`):
  still trusts caller-asserted `X-User-ID` / `X-Org-ID`. The only safe
  upstream caller is now knowledge-mcp (post-Phase B), which forwards
  *verified* identity. Tracked as a follow-up: replace
  `requireAuthOrService` with its own `verify_via_portal` call so
  klai-docs stops depending on its caller's discipline.
- **Phase C** (REQ-3 — scribe), **Phase D** (REQ-4 + REQ-6 —
  retrieval-api + emit_event): see migration sequence above.

---

## Phase C — scribe-api consumer migration (REQ-3)

- Started: 2026-04-28
- Branch: `feature/SPEC-SEC-IDENTITY-ASSERT-001-phase-c` (branched from main)
- Worktree: `C:/Users/markv/stack/02 - Voys/Code/klai-identity-assert-phase-c`

### Phase C architectural decisions

1. **JWT resourceowner is sufficient — no portal verify call.** Per Phase A
   progress.md decision #4 ("primary org from JWT's resourceowner claim,
   preferred — zero extra roundtrip"). Scribe already validates JWT
   signatures against Zitadel JWKS in `app.core.auth`; the resourceowner
   claim cannot be tampered without invalidating the signature, so the
   value is cryptographically authentic. Calling portal-api `/internal/identity/verify`
   on top would be redundant for the JWT-validating consumer.

   This means scribe does **not** depend on `klai-libs/identity-assert`
   in Phase C. REQ-7.4's editable install is skipped for scribe.

2. **Drop `org_id` from `IngestToKBRequest` schema entirely.** No
   transition window with `extra="ignore"` (REQ-3.1's one-sprint window
   is moot pre-prod). The schema-level closure is the cleanest fix for
   the S1 cross-tenant write primitive.

3. **No fast-path-vs-general-path branching.** REQ-3.5 describes a
   "fast path" (skip portal verify when JWT has resourceowner AND user
   has exactly one membership matching). Scribe doesn't have visibility
   into membership counts and doesn't need to — the JWT signature itself
   is the proof. So the "fast path" is the only path. Cleaner code,
   identical security properties for a JWT-validating consumer.

### Delivered

| Component | Change | Tests |
|---|---|---|
| `klai-scribe/scribe-api/app/core/auth.py` | New `CallerIdentity` dataclass + `get_authenticated_caller` dependency that returns `(user_id, org_id)` from a verified Zitadel JWT. Existing `get_current_user_id` shares the new `_decode_zitadel_token` helper. 403 `no_active_org_membership` when JWT lacks `resourceowner` (REQ-3.4). | +5 auth tests |
| `klai-scribe/scribe-api/app/api/transcribe.py` | `IngestToKBRequest.org_id` field removed (REQ-3.1). `ingest_transcription_to_kb` handler swaps `Depends(get_current_user_id)` for `Depends(get_authenticated_caller)`; passes `caller.org_id` to `ingest_scribe_transcript`. | +3 schema/handler tests |

Total Phase C tests: **8 new**. Existing scribe tests unchanged (the new
`get_authenticated_caller` is additive; `get_current_user_id` still works
for endpoints that don't need org_id).

No klai-libs/identity-assert dependency, no Dockerfile change, no compose
change, no workflow change — REQ-3 is a self-contained app-layer fix.

### Follow-up tracked outside Phase C

- **Phase D** (REQ-4 + REQ-6 — retrieval-api + emit_event): see migration
  sequence above.

---

## Phase D — retrieval-api internal-secret hardening (REQ-4 + REQ-6)

- Started: 2026-04-28
- Branch: `feature/SPEC-SEC-IDENTITY-ASSERT-001-phase-d` (branched from main)
- Worktree: `C:/Users/markv/stack/02 - Voys/Code/klai-identity-assert-phase-d`

### Phase D architectural decisions

1. **REQ-4.2 (global verify), confirmed.** Per Phase A progress.md decision
   #2, the internal-secret path runs `verify_body_identity` on every call —
   no `/admin/retrieve` mount-point split. YAGNI for the hypothetical no-
   user-context caller; revisit if such a caller appears.

2. **`X-Caller-Service` is required for any internal-secret request whose
   body carries `user_id`.** Missing → 400 `missing_caller_service`. Unknown
   value → 400 `unknown_caller_service`. Loud failures rather than routing
   through portal verify with an empty service identifier.

3. **Existing `verify_body_identity` is now async.** Both call sites
   (`api/retrieve.py`, `api/chat.py`) await it. The function's contract is
   unified: every successful exit pins `request.state.verified_caller =
   VerifyedCaller(user_id, org_id)` so REQ-6's `emit_event` can source
   tenant identity from a single guaranteed-verified place.

4. **`_get_asserter()` is lazy.** The `IdentityAsserter` is constructed on
   first internal-secret verify call rather than at module load. Keeps
   import-time tests cheap; surfaces missing `PORTAL_API_URL` /
   `PORTAL_INTERNAL_SECRET` config the first time an internal-secret
   request hits the guard.

5. **Admin bypass preserved.** A JWT caller with `role=admin` legitimately
   acts on other users' tenants (REQ-3.1/3.2 of SPEC-SEC-010). For admin
   callers, `verify_body_identity` pins `verified_caller` from the body
   values (not the JWT's own resourceowner) so `emit_event` reflects the
   intended target tenant. Internal-secret callers do NOT inherit this
   bypass — REQ-4.5.

### Delivered

| Component | Change | Tests |
|---|---|---|
| `klai-retrieval-api/retrieval_api/middleware/auth.py` | New `VerifiedCaller` dataclass + module-level lazy `_get_asserter()` + async `verify_body_identity` that handles JWT (existing cross-check + `verified_caller` pin) AND internal-secret (X-Caller-Service header check + portal verify + `verified_caller` pin). | +13 identity tests |
| `klai-retrieval-api/retrieval_api/api/retrieve.py` | `await verify_body_identity(...)` + `emit_event` sources `tenant_id`/`user_id` from `request.state.verified_caller` (REQ-6). Defense-in-depth `product_event_skipped_no_identity` warning when verified pin is unexpectedly absent. | (covered by REQ-4 tests) |
| `klai-retrieval-api/retrieval_api/api/chat.py` | `await verify_body_identity(...)`. | (covered by REQ-4 tests) |
| `klai-retrieval-api/retrieval_api/config.py` | New `portal_api_url` + `portal_internal_secret` settings (no validator — `IdentityAsserter` constructor fail-closes at first use). | n/a |
| `klai-retrieval-api/pyproject.toml` | Editable install of `klai-libs/identity-assert/`. | n/a |
| `klai-retrieval-api/Dockerfile` | Repo-root build context, mirrors klai-knowledge-mcp Phase B pattern so `../klai-libs/identity-assert` resolves inside the container. | n/a |
| `.github/workflows/retrieval-api.yml` | `context: .` + `klai-libs/identity-assert/**` path filter; `pull_request` builds skip push/deploy. | n/a |
| `deploy/docker-compose.yml` | `retrieval-api` service: added `PORTAL_API_URL` + `PORTAL_INTERNAL_SECRET` (latter sourced from existing `PORTAL_API_INTERNAL_SECRET` SOPS entry). | n/a |
| `tests/test_identity_assert.py` | 13 new tests covering REQ-4.2 missing/unknown `X-Caller-Service`, REQ-4.4 portal deny + portal_unreachable fail-closed, REQ-6 `emit_event` reads from verified pin, AC-6 regression (no `emit_event` row when REQ-4 rejects), JWT-path preservation. | n/a |
| `tests/conftest.py` | Auto-fixture `_auto_allow_identity_assert` stubs `_get_asserter()` so existing tests (37 in test_auth.py + many in test_api.py) don't try to reach a real portal. Default `client` headers include `X-Caller-Service: knowledge-mcp`. | n/a |
| `tests/test_auth.py` | Renamed `test_internal_secret_skips_cross_check` → `test_internal_secret_caller_now_verified_against_portal` to reflect REQ-4. | n/a |

### Test results (local, retrieval-api venv)

- `tests/test_identity_assert.py`: 13 / 13 pass
- `tests/test_auth.py::TestCrossUserOrgGuard`: 5 / 5 pass (regression
  guard — JWT cross-check still rejects body mismatches)
- Full `tests/test_auth.py`: 34 pass, 1 pre-existing failure
  (`test_missing_zitadel_audience_fails_import` expects a validator that
  does not exist — pre-existing breakage unrelated to Phase D)

### Pre-existing breakage NOT caused by Phase D

- `test_missing_zitadel_audience_fails_import` (test_auth.py): expects
  `import retrieval_api.config` to fail when `ZITADEL_API_AUDIENCE` is
  empty. The current validator only requires `INTERNAL_SECRET` and
  `REDIS_URL`; the audience field has a graceful-degrade comment.
  Untouched per minimal-changes.

### Follow-up

This is the final phase of SPEC-SEC-IDENTITY-ASSERT-001. After Phase D
deploys and stabilises, the SPEC moves to `status: done` (separate
docs sync PR).

The `klai-docs/lib/auth.ts requireAuthOrService` follow-up (per Phase B
progress.md) is now safe to schedule: every upstream caller of klai-docs
(only knowledge-mcp today) forwards verified identity post-Phase B. A
separate SPEC can replace `requireAuthOrService` with its own
`verify_via_portal` call so klai-docs stops depending on caller
discipline — but it is no longer urgent, since the only caller is now
trustworthy.
