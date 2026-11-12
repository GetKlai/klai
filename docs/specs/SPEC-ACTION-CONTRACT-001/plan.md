# Implementation Plan: SPEC-ACTION-CONTRACT-001

**Status:** draft
**Methodology:** documentation-first, then characterization tests for the first
adopters. This SPEC is a convention; implementation should avoid broad
cross-service refactors.
**Estimated effort:** 1-2 focused backend sessions for phase 1, then
incremental adoption in future action PRs.

---

## Phase ordering

| Phase | Title | Blocker | Owner |
|---|---|---|---|
| 0 | Ratify metadata vocabulary | SPEC review | Backend |
| 1 | Add lightweight local type/checker | Phase 0 | Backend |
| 2 | Adopt on new actions only | Phase 1 | All feature owners |
| 3 | Backfill high-risk existing actions | Phase 2 | Backend/security |
| 4 | Optional shared registry | Real need proven | Backend |

---

## Phase 0 - Ratify vocabulary

Review and freeze the initial enum values from `spec.md`:

- `kind`
- `auth.mode`
- `effects.access`
- `execution.concurrency_class`
- `failure.mode`

Output of this phase:

- Update `spec.md` if review finds missing values.
- No code changes required.

---

## Phase 1 - Add lightweight validation

Goal: make missing metadata visible in review without forcing a runtime
framework.

Recommended implementation:

- Add a small test helper or lint script that can validate ActionSpec dicts.
- Prefer a local Python dataclass/Pydantic model only in services that already
  have a natural place for it.
- Do not make every service import a new shared package in this phase.

Candidate files:

- `klai-knowledge-mcp/tests/` for MCP examples
- `deploy/litellm/tests/` for hook sub-action examples
- `klai-knowledge-ingest/tests/test_queues_constants.py` for queue/lane mapping

Validation should check:

- required fields exist,
- enum values are valid,
- `timeout_ms` is present for HTTP/model calls,
- destructive actions have explicit `destructive: true`,
- model-facing actions have a `result_policy`.

---

## Phase 2 - Adopt on new actions

Every new action in scope adds metadata in the same PR that adds the action.

Review checklist:

1. Does `action_id` uniquely identify the callable boundary?
2. Are auth and tenant/user identity source explicit?
3. Is read/write/destructive behaviour explicit?
4. Does the concurrency class match the actual workload?
5. Is timeout explicit and tested?
6. Is failure mode deliberate?
7. Are telemetry events and privacy policy named?
8. Is model-facing output capped and leak-guarded?
9. Are tests/acceptance cases listed?

This phase is the main value of the SPEC. It prevents new drift while avoiding
a disruptive old-code migration.

---

## Phase 3 - Backfill high-risk existing actions

Backfill only actions that are security-sensitive, model-facing, destructive,
or likely to be copied.

Initial candidates:

- `klai-knowledge-mcp/main.py::search_knowledge`
- `klai-knowledge-mcp/main.py::save_personal_knowledge`
- `klai-knowledge-mcp/main.py::save_org_knowledge`
- `klai-knowledge-mcp/main.py::save_to_docs`
- `deploy/litellm/klai_knowledge.py` retrieval call inside
  `KlaiKnowledgeHook.async_pre_call_hook`
- `deploy/litellm/klai_knowledge.py::_get_templates`
- `deploy/litellm/klai_knowledge.py::_rewrite_and_classify`
- destructive connector-delete / purge actions
- new or recently changed Procrastinate task queues

Do not backfill every helper in one PR. Tie metadata backfill to a nearby
change or a focused security hardening pass.

---

## Phase 4 - Optional shared registry

Only add a shared registry if at least two concrete needs appear, for example:

- docs generation from ActionSpecs,
- automated Grafana dashboard labels,
- policy checks in CI across multiple services,
- runtime action discovery for an admin UI.

Until then, duplication of a small metadata block is cheaper than another
cross-service dependency.

---

## Rollout safety

- Phase 1 is test-only/docs-only; no production behaviour changes.
- Phase 2 applies to new actions only.
- Phase 3 is incremental and can be skipped for untouched legacy actions.
- Existing action behaviour remains governed by the owning SPECs listed in
  `related`.

