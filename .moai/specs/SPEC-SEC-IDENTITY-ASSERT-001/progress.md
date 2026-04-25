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
| REQ-1 | ✅ | 20 | `/internal/identity/verify` endpoint; service + Redis cache + structlog |
| REQ-5 | ✅ | 17 | `_notebook_filter` + ingest payload + retrieval guard |
| REQ-7 | ✅ | 39 | `klai-libs/identity-assert/` shared library |

Total: **76 tests passing** for SPEC-SEC-IDENTITY-ASSERT-001 in this branch.

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
