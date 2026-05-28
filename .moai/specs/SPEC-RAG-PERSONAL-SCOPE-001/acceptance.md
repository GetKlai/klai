# Acceptance criteria: SPEC-RAG-PERSONAL-SCOPE-001

## Definition of Done

This SPEC is "done" when all of the following are true on `main`:

1. The shared library `klai-libs/kb-slugs` exists, is consumed by both `klai-portal` and `klai-retrieval-api`, and ships its own unit tests + drift test.
2. Retrieval-api `_scope_filter` always appends the canonical-slug condition for `scope=personal` (REQ-2) and the personal portion of `scope=both` (REQ-3).
3. The Jantine regression test (REQ-7) reproduces the live bug on main and passes after the fix.
4. The structured log event (REQ-8) appears in VictoriaLogs at least once after deploy.
5. All four test suites are green: `klai-libs/kb-slugs/tests`, `klai-portal/backend/tests`, `klai-retrieval-api/tests`, `deploy/litellm/tests`.
6. Lint clean on retrieval-api + portal-api (`ruff check` + `ruff format --check`).
7. Manual e2e verification documented in the PR description (Phase 6 of plan.md).

## Test matrix

### Library tests (`klai-libs/kb-slugs/tests/test_personal_kb_slug.py`)

| Test ID | Description | Pass criterion |
|---|---|---|
| LIB-T1 | `personal_kb_slug("u1")` returns `"personal-u1"` | string equality |
| LIB-T2 | `personal_kb_slug("")` returns `"personal-"` | edge case — empty user_id allowed at lib level; validation lives in retrieval-api |
| LIB-T3 | Function is importable from `klai_kb_slugs` namespace | `from klai_kb_slugs import personal_kb_slug` succeeds |
| LIB-T4 | `__all__` contains `personal_kb_slug` | for re-export safety |

### Portal-api drift test (`klai-portal/backend/tests/test_personal_kb_slug_drift.py`)

| Test ID | Description | Pass criterion |
|---|---|---|
| PORTAL-T1 | Portal's `personal_kb_slug` re-export matches the lib output | `from app.services.default_knowledge_bases import personal_kb_slug; assert personal_kb_slug("uX") == "personal-uX"` |
| PORTAL-T2 | Portal's output equals the inline-literal pattern | `assert personal_kb_slug(user_id) == f"personal-{user_id}"` for several user_ids — guards against accidental rename |
| PORTAL-T3 | Existing call-sites still import correctly | `assert callable(personal_kb_slug)` and signature unchanged |

### Retrieval-api scope-filter tests (`klai-retrieval-api/tests/test_scope_filter.py`)

| Test ID | Description | Pass criterion |
|---|---|---|
| SCOPE-T1 | `scope=personal` + `user_id=u1` → conditions include `kb_slug=personal-u1` | search `conditions` for a `FieldCondition(key='kb_slug', match=MatchValue('personal-u1'))` |
| SCOPE-T2 | `scope=personal` + `effective_role=personal` → canonical filter still present | same assertion as SCOPE-T1, with `effective_role` set |
| SCOPE-T3 | `scope=both` + `user_id=u1` → `visibility_should` private branch carries `kb_slug=personal-u1` | navigate the filter tree; assert canonical slug inside the `must` of the private-branch Filter |
| SCOPE-T4 | `scope=org` + `user_id=u1` → no canonical slug appended (scope=org has no personal portion) | assert no `kb_slug=personal-u1` appears in conditions |
| SCOPE-T5 | `scope=personal` + `user_id=None` → no canonical slug appended (no user to derive from); but `retrieve` endpoint should have returned 400 before reaching this point | unit on `_scope_filter` only — `_scope_filter` is permissive when user_id absent; endpoint enforces |
| SCOPE-T6 (regression) | Existing `test_kb_slugs_both_scope_with_user_bypasses_personal_chunks` updated to assert the new canonical-narrowed shape | test passes after fix |

### Retrieval-api role-filter tests (`klai-retrieval-api/tests/test_role_filter.py`)

| Test ID | Description | Pass criterion |
|---|---|---|
| ROLE-T1 | `test_personal_role_org_scope_becomes_personal` unchanged | stays green |
| ROLE-T2 | `test_personal_role_clears_kb_slugs` unchanged | stays green |
| ROLE-T3 (new) | `test_personal_role_stripped_slugs_still_canonical_narrowed`: build a request with `effective_role=personal, scope=org, kb_slugs=["sneaky-org"]`. Apply `_apply_role_rewrite` then `_scope_filter`. Assert: scope becomes personal, kb_slugs becomes None, BUT canonical slug filter is appended | new assertion combining strip + canonical narrowing |

### Retrieval-api regression test (`klai-retrieval-api/tests/test_personal_scope_canonical_regression.py`)

| Test ID | Description | Pass criterion |
|---|---|---|
| REG-T1 (Jantine, personal role) | scope=personal, user_id=U, effective_role=personal, no kb_slugs supplied → exactly one `kb_slug` field condition AND it equals `personal-U` | exactly one canonical filter |
| REG-T2 (Jantine, admin role) | Same as REG-T1 but effective_role=admin | exactly one canonical filter |
| REG-T3 (Jantine, company role) | Same as REG-T1 but effective_role=company | exactly one canonical filter |
| REG-T4 (proves bug existed) | If run against main without the fix: REG-T1 fails (no canonical filter applied for personal-role) | documented as expected pre-fix red |

### Integration test (`klai-retrieval-api/tests/test_api.py`)

| Test ID | Description | Pass criterion |
|---|---|---|
| API-T1 | POST /retrieve with scope=personal + valid user_id → 200 + filtered chunks | response body has chunks with `kb_slug=personal-<user>` only |
| API-T2 | POST /retrieve with scope=personal + missing user_id → 400 with detail "user_id required" | existing test stays green |
| API-T3 | POST /retrieve with scope=both + valid user_id + kb_slugs=[org1] → 200; personal chunks restricted to canonical slug | filter tree contains canonical-narrowed private branch |

### LiteLLM hook tests (`deploy/litellm/tests/test_klai_knowledge_hook.py`)

| Test ID | Description | Pass criterion |
|---|---|---|
| HOOK-T1 | Existing `test_empty_slugs_and_personal_on_narrows_to_canonical_personal_kb` | unchanged, still green |
| HOOK-T2 | Existing `test_empty_slugs_and_personal_on_falls_through_when_portal_omits_slug` | unchanged, still green |

Hook tests require ZERO changes — the hook keeps sending the client-side filter; the new server-side filter is additive.

## Manual e2e checklist (post-deploy)

| Step | Expected outcome |
|---|---|
| Login as a `personal`-role test user with two private KBs: canonical Persoonlijk + a user-created KB `test2` | login succeeds |
| Upload a unique-content document to `test2` (e.g., "secret-marker-12345") | upload succeeds, doc visible in test2 |
| Open chat with only "Persoonlijk" selected in dropdown | dropdown reflects single selection |
| Ask: "wat staat er over secret-marker-12345" | response either says "Ik kan dit niet betrouwbaar beantwoorden" OR retrieves canonical-KB content only (no test2 content) |
| Same query with "Persoonlijk" + "test2" selected | response surfaces test2 content correctly |
| VictoriaLogs query: `service:retrieval-api AND event:retrieval_personal_scope_canonical_filter_applied` over last 30 min | ≥1 event present |

## Out-of-scope verifications (deferred)

- Performance impact of the extra `FieldCondition` on Qdrant search latency. Expected zero-impact (O(1) filter); not benchmarked.
- Migration of existing chunks to add `kb_kind=personal_canonical` metadata. Future SPEC.
- Removal of LiteLLM hook's redundant client-side filter. Intentionally kept as defense-in-depth.

## Sign-off

- [ ] Code review approved (focus areas: shared lib drift test + scope_filter canonical condition placement)
- [ ] All test suites green on the feature branch
- [ ] Manual e2e checklist completed and recorded in PR description
- [ ] Grafana REQ-8 log event verified post-deploy
- [ ] Deploy comms drafted ("Persoonlijk dropdown now surfaces only the canonical KB; non-canonical user-private KBs must be selected explicitly")
