---
id: SPEC-ACTION-CONTRACT-001
version: "0.1.0"
status: draft
created: 2026-05-08
updated: 2026-05-08
author: Mark Vletter
priority: high
issue_number: null
related:
  - SPEC-MCP-RETRIEVAL-001
  - SPEC-MCP-AUTH-001
  - SPEC-SEC-IDENTITY-ASSERT-001
  - SPEC-SEC-SERVICE-AUTH-001
  - SPEC-WORKER-LANES-001
---

# SPEC-ACTION-CONTRACT-001: ActionSpec convention for model-facing and service-boundary actions

## HISTORY

| Datum | Versie | Wijziging |
|-------|--------|-----------|
| 2026-05-08 | 0.1.0 | Initial draft. Introduces a pragmatic `ActionSpec` convention for new Klai actions that are model-facing or cross a service boundary: MCP tools, connector actions, retrieval stages, Procrastinate tasks, and LiteLLM hook actions. This is a documentation/test convention first, not a broad framework rewrite. |

---

## Summary

Klai now has several places where code exposes "actions" to a model or
crosses an internal service boundary:

- MCP tools in `klai-knowledge-mcp/main.py` (`save_*`, `search_knowledge`)
- LiteLLM hook actions in `deploy/litellm/klai_knowledge.py` (feature lookup,
  query rewrite, taxonomy classify, retrieval, telemetry)
- retrieval-api stages reached by `/retrieve`
- connector and ingest operations that enqueue Procrastinate tasks
- worker queue/lane assignments in `klai-knowledge-ingest/knowledge_ingest/queues.py`

Those actions already carry implicit contracts: identity source, auth mode,
read/write semantics, timeout, fail-open/fail-closed behaviour, telemetry, and
result-shape. Today those contracts are spread across docstrings, comments,
tests, and service-specific specs. That makes new action additions easy to
under-specify, especially when the caller is an LLM or when a service acts on
behalf of a tenant/user.

This SPEC defines a lightweight `ActionSpec` convention. New model-facing and
service-boundary actions must declare a small metadata block next to the action
entrypoint or in the owning SPEC. The convention is intentionally boring:
structured metadata, local tests, and checklist enforcement. It does **not**
require refactoring existing services into one common action framework.

---

## Problem

Klai has learned the same boundary lesson in multiple areas:

- `SPEC-SEC-IDENTITY-ASSERT-001` showed that a shared service secret proves
  network identity, not tenant identity.
- `SPEC-MCP-RETRIEVAL-001` needed explicit tool result shape, `top_k` bounds,
  timeout, telemetry, and `ToolError` behaviour.
- `SPEC-WORKER-LANES-001` made queue lane/concurrency an explicit contract
  because "just add a task" silently starved user-triggered work.
- The LiteLLM hook has several deliberate fail-open/fail-loud decisions. Some
  are user-protective, some are availability-protective, and they are easy to
  miscopy into the wrong surface.

The missing abstraction is not "one executor for everything". The missing
abstraction is a small action contract that forces engineers and review agents
to answer the same safety questions before adding a callable boundary.

---

## Scope

In scope:

- Define the `ActionSpec` metadata schema and naming convention.
- Require the convention for **new** actions that are model-facing or cross a
  service boundary.
- Add a migration path for high-risk existing actions without blocking feature
  work.
- Define minimal validation/test expectations.
- Cover Python services first, because the current action surfaces are mostly
  Python (`klai-knowledge-mcp`, `deploy/litellm`, `klai-knowledge-ingest`,
  `klai-retrieval-api`, `klai-connector`).

Out of scope:

- Refactoring all existing MCP tools, LiteLLM helpers, retrieval stages, and
  Procrastinate tasks in one PR.
- Introducing a runtime registry that all services must import.
- Changing auth semantics, retrieval semantics, or queue lane assignments.
- Replacing Procrastinate, FastMCP, LiteLLM hooks, connector clients, or
  retrieval-api route structure.

---

## Definition: Action

An **Action** is any callable unit that one of these actors can trigger:

1. a model or model host (MCP tool, LiteLLM tool/hook, agent connector action),
2. another Klai service over HTTP/RPC,
3. an async worker queue, or
4. a retrieval/ingest pipeline stage that changes data visibility, makes an
   external call, emits telemetry, or affects user-facing answers.

Pure internal helper functions are not actions unless they are the boundary
where one of the properties above becomes true.

---

## ActionSpec metadata

Every new action in scope SHALL have an `ActionSpec` metadata block with these
fields. The preferred representation is a typed Python dataclass or Pydantic
model when a service already has a local pattern for configuration models; a
YAML-ish comment block in the owning SPEC is acceptable for the first phase.

```yaml
action_id: knowledge-mcp.search_knowledge
owner_service: klai-knowledge-mcp
entrypoint: klai-knowledge-mcp/main.py::search_knowledge
kind: mcp_tool

input:
  schema: search_knowledge(query: str, top_k: int = 8)  # function signature; no request model exists
  validation:
    query.max_chars: 2000
    top_k: clamp[1,15]

auth:
  mode: oauth_or_internal_secret
  caller_identity: oauth_client | librechat_internal
  tenant_identity:
    requires_user_id: true
    requires_org_id: true
    source: _identify_request(ctx)
    verified_by: portal-api /internal/mcp-token/verify or /internal/identity/verify

effects:
  access: read
  destructive: false
  external_calls:
    - retrieval-api /retrieve

execution:
  concurrency_class: interactive_io
  timeout_ms: 3000
  retry_policy: none
  idempotency: read_only

failure:
  mode: fail_closed
  user_surface: ToolError generic bilingual message
  log_fields:
    - action_id
    - status_code
    - elapsed_ms

telemetry:
  events:
    - retrieval_log
    - product_events.knowledge.queried
    - gap_event
  correlation:
    - request_id
    - org_id
    - user_id
    - caller_client_id

result_policy:
  max_items: 15
  max_text_chars_per_item: service-local cap
  secret_redaction: required
  cross_tenant_leak_guard: retrieval-api RLS + verified identity

tests:
  unit:
    - input bounds
    - auth/identity failure
    - timeout/failure mode
    - telemetry fire-and-forget
  integration:
    - cross-tenant isolation where data is tenant-scoped

docs:
  spec: .moai/specs/SPEC-MCP-RETRIEVAL-001/spec.md
  runbook: optional
```

### Required fields

**REQ-1.** `action_id` SHALL be stable, globally searchable, and lower-case:
`{service_or_domain}.{action_name}`. Examples:
`knowledge-mcp.search_knowledge`, `litellm.knowledge_retrieve`,
`knowledge-ingest.run_crawl`, `connector.delete_connector`.

**REQ-2.** `kind` SHALL use one of:
`mcp_tool`, `connector_action`, `retrieval_stage`, `procrastinate_task`,
`litellm_hook_action`, `http_endpoint`, `scheduled_task`, or
`internal_boundary`.

**REQ-3.** `input.schema` SHALL name the request model, function signature, or
typed payload contract. Free-form `dict` payloads are allowed only when the
field-level validation rules are listed in `input.validation`.

**REQ-4.** `auth.mode` SHALL state how the caller is authenticated. Valid
initial values:
`oauth_user`, `oauth_client`, `internal_secret`, `service_jwt`,
`oauth_or_internal_secret`, `public_none`, `worker_internal`.

**REQ-5.** `auth.tenant_identity` SHALL state whether `user_id`, `org_id`,
`org_slug`, `kb_slug`, `connector_id`, or equivalent scope fields are required,
where they come from, and how they are verified. If the action carries a
caller-supplied tenant/user value across a service boundary, the verification
mechanism must be named.

**REQ-6.** `effects.access` SHALL be one of `read`, `write`, `read_write`, or
`none`. `effects.destructive` SHALL be explicit `true` or `false`.

**REQ-7.** `execution.concurrency_class` SHALL be one of:
`interactive_io`, `bulk_io`, `llm`, `cpu`, `operator`, or `unknown`.
For Procrastinate tasks this maps to the queue lane (`IO_QUEUES` or
`LLM_QUEUES`). For non-worker actions it documents expected latency and
upstream pressure.

**REQ-8.** `execution.timeout_ms` SHALL be explicit for every action that makes
HTTP calls, model calls, queue waits, file/garage access, or database operations
outside the local request transaction. "Uses default timeout" is not an
acceptable value.

**REQ-9.** `failure.mode` SHALL be `fail_open`, `fail_closed`, or
`fail_loud_degraded`.

- `fail_open`: continue without the optional action result, log/telemetry only.
- `fail_closed`: abort the action and return a safe error.
- `fail_loud_degraded`: continue with degraded answer/data but explicitly tell
  the model/user that the protected dependency failed.

**REQ-10.** `telemetry.events` SHALL list emitted events/log streams or state
`none` with a reason. Model-facing and tenant-scoped actions should default to
at least one structured event unless the action is too low-volume or
privacy-sensitive.

**REQ-11.** `result_policy` SHALL state result-size caps and data-leak guards
for any model-facing result. Tool results must not rely on "the model will be
careful" as the only leak guard.

**REQ-12.** `tests` SHALL include at least one test or acceptance scenario for
auth/identity, input bounds, failure mode, and result policy when those fields
are nontrivial.

**REQ-13.** `docs.spec` SHALL point to the owning SPEC or local markdown file.
If an action is added without a SPEC, the metadata block in code is the
documentation source of truth.

---

## Placement

**REQ-14.** For new actions, the ActionSpec SHALL live adjacent to the
entrypoint when practical:

- MCP tool: directly above the `@mcp.tool` function or in
  `klai-knowledge-mcp/action_specs.py` imported by tests.
- LiteLLM hook sub-action: near the helper that performs the boundary call
  (`_get_kb_feature`, `_rewrite_and_classify`, retrieval call, telemetry emit).
- Procrastinate task: near the task registration and queue constant.
- Connector action: near the HTTP client method and route handler.
- Retrieval stage: near the request/response model or route handler.

**REQ-15.** If a service cannot yet import a shared `ActionSpec` type without
creating deployment churn, it MAY use a `# ACTION_SPEC:` comment block in
phase 1. The block must still contain the required fields.

**REQ-16.** Existing actions do not need immediate metadata unless they are
being materially changed. Material changes include auth semantics, identity
source, write/destructive behaviour, result shape, timeout, retry policy,
queue/lane, or telemetry shape.

---

## Baseline classifications for known surfaces

These are not implementation mandates; they are the initial classification
targets for future incremental metadata additions.

| Surface | Example | Kind | Access | Failure mode | Concurrency |
|---|---|---|---|---|---|
| MCP save personal/org/docs | `save_personal_knowledge`, `save_org_knowledge`, `save_to_docs` | `mcp_tool` | write | fail_closed | interactive_io |
| MCP search | `search_knowledge` | `mcp_tool` | read | fail_closed | interactive_io |
| LiteLLM feature gate | `_get_kb_feature` | `litellm_hook_action` | read | fail_closed for entitlement | interactive_io |
| LiteLLM templates | `_get_templates` | `litellm_hook_action` | read | fail_open | interactive_io |
| LiteLLM query rewrite | `_rewrite_and_classify` | `litellm_hook_action` | read | fail_open | llm |
| LiteLLM retrieval | `/retrieve` call in hook | `litellm_hook_action` | read | fail_loud_degraded | interactive_io |
| Ingest queue task | `run_crawl`, enrichment tasks | `procrastinate_task` | write/read_write | task-specific | `IO_QUEUES` or `LLM_QUEUES` |
| Connector delete/purge | connector purge task | `procrastinate_task` / `connector_action` | read_write | fail_closed where destructive | bulk_io |

---

## Design decisions

### A1. Convention before framework

The first version is a contract convention plus tests. A shared Python package
is allowed later, but not required now. The current services deploy
independently; forcing every surface to import one registry would create more
risk than value.

### A2. ActionSpec documents model risk and service-boundary risk together

MCP tools and LiteLLM hook actions are model-facing. Procrastinate tasks and
connector actions are service-boundary operations. The same metadata catches
both classes: identity, effects, concurrency, timeout, failure mode, telemetry,
and result policy.

### A3. Failure mode is first-class

Klai intentionally uses different behaviours:

- templates fail open because missing style guidance should not break chat,
- entitlement and identity fail closed,
- retrieval failure in LibreChat fails loud/degraded so the model warns the
  user instead of pretending the KB was empty,
- MCP retrieval failure raises `ToolError`.

Those choices must be named per action. Copying one surface's failure mode into
another is a bug unless the ActionSpec says it is intended.

### A4. Concurrency class is part of the action contract

`SPEC-WORKER-LANES-001` showed that latency profile and queue lane are
architecture, not tuning. Every new queued or long-running action must say
whether it is I/O, LLM, CPU, or operator work before it lands.

### A5. Result policy belongs beside model-facing output

Actions that return data to a model must cap output size and state leak guards.
The model may format or cite the result, but the action owns the data boundary.

---

## Non-goals

1. No mass migration of every existing helper in `deploy/litellm/klai_knowledge.py`.
2. No single `ActionExecutor` abstraction.
3. No central permission system replacement.
4. No new database tables.
5. No runtime discovery API for actions in v0.1.0.
6. No change to customer-visible behaviour.

---

## Implementation plan

See `plan.md`.

---

## Acceptance criteria

See `acceptance.md`.

