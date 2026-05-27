---
id: SPEC-RAG-PERSONAL-SCOPE-001
version: "0.1.0"
status: draft
created: 2026-05-27
updated: 2026-05-27
author: Mark Vletter
priority: high
related:
  - SPEC-PORTAL-RBAC-REFACTOR-001 (REQ-17 — personal-role kb_slugs strip; this SPEC adds canonical filter alongside)
  - SPEC-SEC-IDENTITY-ASSERT-001 (verify_body_identity gives us the trusted user_id this SPEC consumes)
  - SPEC-PORTAL-KB-OWNERSHIP-001 (route-level firewall on portal-api uses the same canonical slug)
prs:
  - "#705 — initial client-side narrowing (Jantine fix; admin-only effective)"
  - "#716 — personal_kb_slug field on KnowledgeFeatureResponse"
---

# HISTORY

| Version | Date       | Author       | Change                                               |
|---------|------------|--------------|------------------------------------------------------|
| 0.1.0   | 2026-05-27 | Mark Vletter | Initial draft — surface personal-role leak after #705 ships |

---

# SPEC-RAG-PERSONAL-SCOPE-001: Server-side enforcement of Persoonlijk-KB narrowing

## Summary

Retrieval-api MUST narrow `scope=personal` (and the personal portion of `scope=both`) to the requester's canonical Persoonlijk-KB, regardless of which `kb_slugs` the caller supplied and regardless of the caller's `effective_role`. The slug template lives in exactly one place — a new shared library `klai-libs/kb-slugs` — that both `klai-portal` and `klai-retrieval-api` import. Defense-in-depth replaces the current client-side-only narrowing, which is silently bypassed for personal-role callers by the existing RBAC strip rule.

## Motivation

The 2026-05-27 Jantine incident exposed a leak where the Persoonlijk dropdown returned chunks from other user-owned private KBs. PR #705 patched it client-side in the LiteLLM-hook. Code review on 2026-05-27 evening surfaced that retrieval-api's `_apply_role_rewrite` (`klai-retrieval-api/retrieval_api/api/retrieve.py:258-262`) **strips** every `kb_slugs` value for callers whose `effective_role == "personal"`. Because new users join as `personal` by default (SPEC-PORTAL-RBAC-REFACTOR-001 REQ-11), the majority of production users continue to see the leak after PR #705 / #715 / #716 shipped.

Three diagnostic facts shape this SPEC:

- **Defense-in-depth is missing.** The client (hook) is the only narrowing gate today. A buggy or future caller that omits the slug filter re-opens the leak. Retrieval-api must enforce.
- **scope=both is also leaky** (lower priority). `kb_personal=True + kb_slugs=[org_slugs]` translates to `scope=both, kb_slugs=[org_slugs]`. The current visibility-should clause lets every `user_id=requester` chunk through, including non-canonical user-private KBs.
- **No drift acceptable.** Putting the slug template (`personal-<zitadel_user_id>`) in both portal-api and retrieval-api creates the exact `url-shape-multi-file-drift` pattern we have a pitfall rule against. A shared library is the only safe place for the template.

## Out of scope

- Re-stamping existing Qdrant chunks with a new `kb_kind` payload field (Option D in research.md). Larger refactor, deferred.
- Replacing the `scope` enum with kb_slugs-only semantics across the API. Long-term refactor, deferred.
- Frontend dropdown UX changes. The widget UI is unchanged; the wire contract from the LiteLLM-hook stays additive.
- Performance optimisation of the qdrant filter (extra `FieldCondition` is O(1)).

## Requirements (EARS format)

### REQ-1 — Shared slug-template library

The repository SHALL contain a new package `klai-libs/kb-slugs` exposing a single function `personal_kb_slug(user_id: str) -> str` that returns `f"personal-{user_id}"`. The package SHALL be importable by `klai-portal` (replacing the inline helper in `app.services.default_knowledge_bases`) and by `klai-retrieval-api`. The package SHALL ship a unit test pinning the exact string format with at least one positive sample and one validation against the legacy inline output.

Acceptance:
- `klai-libs/kb-slugs/klai_kb_slugs/__init__.py` exports `personal_kb_slug`.
- `klai-portal/backend/app/services/default_knowledge_bases.py` re-exports the lib's function (no behavioural change).
- A drift test in `klai-portal/backend/tests/test_personal_kb_slug_drift.py` asserts `personal_kb_slug("u1")` returns `"personal-u1"` AND equals the legacy inline string `f"personal-{u1}"`.

### REQ-2 — Server-side narrowing for scope=personal

WHEN `req.scope == "personal"`, retrieval-api `_scope_filter` SHALL unconditionally append `FieldCondition(key="kb_slug", match=MatchValue(value=personal_kb_slug(req.user_id)))` to the must-conditions list, irrespective of the value of `req.kb_slugs` or `req.effective_role`. The new condition SHALL be appended AFTER the existing `user_id = req.user_id` condition so both must match.

Acceptance:
- `test_scope_filter::test_scope_personal_narrows_to_canonical_slug` passes (new test).
- `test_scope_filter::test_scope_personal_personal_role_still_narrows_to_canonical` passes (new test — covers the RBAC-strip path).
- Existing `test_scope_filter` assertions on the user_id filter remain green.

### REQ-3 — Server-side narrowing for scope=both, personal portion

WHEN `req.scope == "both"`, retrieval-api `_scope_filter` SHALL replace the existing `(visibility=private AND user_id=U)` Filter inside `visibility_should` with `(visibility=private AND user_id=U AND kb_slug=personal_kb_slug(req.user_id))`. The non-private branch (`visibility != private`) SHALL remain unchanged.

Acceptance:
- `test_scope_filter::test_scope_both_personal_portion_narrows_to_canonical` passes (new test).
- `test_scope_both_kb_slugs_with_user_bypasses_personal_chunks` (existing test) is updated to assert the new bypass shape (only canonical slug passes via user_id-bypass).

### REQ-4 — user_id requirement remains the precondition

The current 400 ("user_id required for scope=personal/both") in `retrieve()` SHALL continue to fire when `scope in ("personal", "both")` and `user_id` is missing. The new canonical-slug filter MUST NOT mask the missing-user_id error path.

Acceptance:
- `test_retrieve_missing_user_id_returns_400_for_personal` stays green.
- `test_retrieve_missing_user_id_returns_400_for_both` stays green.
- Code review verifies the 400 check fires before `_scope_filter` is invoked.

### REQ-5 — RBAC strip rule preserved

The existing personal-role rewrite (`klai-retrieval-api/retrieval_api/api/retrieve.py:258-262`) SHALL remain unchanged in behaviour. Specifically: personal-role callers that supply `scope=org` or `kb_slugs=[<org_slug>]` SHALL still have their request rewritten (scope → personal, kb_slugs → None). The new canonical-narrowing is additive; the strip remains the RBAC guard against ORG-KB sneaking.

Acceptance:
- `test_role_filter::test_personal_role_org_scope_becomes_personal` stays green.
- `test_role_filter::test_personal_role_clears_kb_slugs` stays green.
- A new test `test_personal_role_stripped_slugs_still_canonical_narrowed` asserts the chained behaviour: kb_slugs stripped to None, but canonical slug filter still applied by `_scope_filter`.

### REQ-6 — Caller contract unchanged for non-personal scopes

The LiteLLM-hook, knowledge-mcp, and partner_chat SHALL require no contract changes:
- LiteLLM-hook's PR #715 client-side filter (`kb_slugs=[personal-<user>]`) MAY remain in place as a redundant client-side narrowing. It SHALL NOT be removed in this SPEC.
- knowledge-mcp (scope=both, kb_slugs=None) SHALL keep returning the broad "everything I own + org chunks" set; the new canonical narrowing for scope=both only filters the personal portion, not the org portion. MCP traffic against non-canonical user-owned chunks (test2 etc.) STOPS in line with the new contract — accept this as an intentional behaviour tightening for third-party MCP traffic.
- partner_chat (scope=org) is untouched by this SPEC.

Acceptance:
- LiteLLM hook tests in `deploy/litellm/tests/test_klai_knowledge_hook.py` stay green without modification.
- knowledge-mcp tests stay green without modification.
- partner_chat tests stay green without modification.

### REQ-7 — Regression test for the Jantine scenario

A new end-to-end test in `klai-retrieval-api/tests/` SHALL reproduce the Jantine bug shape and assert it does not leak:
- Setup: two private KBs owned by the same user, slugs `personal-U` and `test2`. User_id = U. Effective_role = personal.
- Request: `scope=personal`, kb_slugs=None or kb_slugs=[`personal-U`].
- Assertion: only `personal-U` chunks appear in the response; zero chunks from `test2`.

Acceptance:
- `test_personal_scope_excludes_non_canonical_user_owned_kbs` passes for both `effective_role=personal` and `effective_role=admin`.
- The same test, when run against the current main branch code, fails — proves the test reproduces the live bug.

### REQ-8 — Observability

When `_scope_filter` appends the canonical slug condition, it SHALL log a structured event at INFO level once per request:

```
event: retrieval_personal_scope_canonical_filter_applied
org_id: ...
user_id: ...
canonical_slug: personal-<user_id>
scope: personal | both
effective_role: ...
client_supplied_kb_slugs: [...] | null
```

This lets us verify in VictoriaLogs that the filter fires for every relevant request after deploy, and lets us spot-check that the slug template did not silently change.

Acceptance:
- Grafana query `service:retrieval-api AND event:retrieval_personal_scope_canonical_filter_applied` returns >0 within 1 hour of deploy.

## Success criteria

- [ ] REQ-1: shared library `klai-libs/kb-slugs` exists, both services import it, drift test green
- [ ] REQ-2: scope=personal applies canonical slug filter regardless of role
- [ ] REQ-3: scope=both narrows personal portion to canonical slug
- [ ] REQ-4: user_id validation 400 still fires before _scope_filter
- [ ] REQ-5: RBAC strip behaviour preserved
- [ ] REQ-6: no contract change for any external caller
- [ ] REQ-7: regression test reproduces the live bug and passes after the fix
- [ ] REQ-8: structured log event visible in VictoriaLogs
- [ ] All existing klai-retrieval-api tests pass
- [ ] All existing deploy/litellm tests pass
- [ ] All existing klai-portal/backend tests pass

## Test plan

See `acceptance.md` for the full test matrix.

## Implementation references

- `klai-retrieval-api/retrieval_api/services/search.py:69-115` (`_scope_filter`)
- `klai-retrieval-api/retrieval_api/api/retrieve.py:252-262` (validation + RBAC strip)
- `klai-portal/backend/app/services/default_knowledge_bases.py:22-24` (current inline helper)
- `klai-libs/chat-prompts/klai_chat_prompts/__init__.py` (pattern reference for the new shared lib)
- `deploy/litellm/klai_knowledge.py:1944-1993` (existing client-side narrowing for comparison)

## Rollout

1. Land in a single PR (no flag, no migration). The new filter is additive — the behaviour change is that some chunks STOP being returned. Loud test failures on regression-tests are the canary.
2. Post-merge: run a manual e2e check with a non-admin test user — confirm Persoonlijk dropdown returns only canonical KB chunks.
3. Within 1 hour of deploy: query Grafana for REQ-8 log event. Zero events ≠ rollout failure (could mean no personal-scope traffic in the window); but presence confirms the filter is wired.
4. No backout plan needed — the filter is server-side, no client coordination, revert is a single Git revert.
