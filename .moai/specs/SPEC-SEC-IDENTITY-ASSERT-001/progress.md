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

1. ✅ This branch: REQ-1 + REQ-7 + REQ-5
2. Next: REQ-2 (knowledge-mcp) — consumer of REQ-1
3. Then: REQ-3 (scribe) — independent consumer
4. Then: REQ-4 (retrieval-api) — independent consumer; unblocks REQ-6
5. Last: REQ-6 (emit_event identity) — depends on REQ-4

Each phase is independently revertable via the `IDENTITY_VERIFY_MODE` flag
documented in `research.md` §5.1.
