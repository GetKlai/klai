# Acceptance Criteria: SPEC-ACTION-CONTRACT-001

Given/When/Then scenarios that must pass before this SPEC is considered useful
as an engineering convention.

---

## AC-1: New MCP tool includes ActionSpec metadata

**Given** a PR adds a new `@mcp.tool` to `klai-knowledge-mcp`
**When** the PR is reviewed
**Then** the action has an `ActionSpec` with `action_id`, `kind`,
`input.schema`, `auth.mode`, `auth.tenant_identity`, `effects`,
`execution.timeout_ms`, `failure.mode`, `telemetry`, `result_policy`, `tests`,
and `docs`
**And** the tests cover input bounds, auth/identity failure, timeout behaviour,
and result-size policy where applicable

## AC-2: New Procrastinate task declares lane and effects

**Given** a PR adds a new Procrastinate task or queue in
`klai-knowledge-ingest`
**When** the task is added
**Then** its `ActionSpec` declares `kind=procrastinate_task`
**And** `execution.concurrency_class` maps to either `IO_QUEUES` or
`LLM_QUEUES`
**And** the lane partition test fails if the queue is in neither lane or both
lanes
**And** write/destructive behaviour is explicit

## AC-3: New service-boundary HTTP action declares tenant identity source

**Given** a PR adds a service-to-service HTTP call that carries `user_id`,
`org_id`, `org_slug`, `kb_slug`, or `connector_id`
**When** the ActionSpec is inspected
**Then** `auth.tenant_identity.source` names where the value comes from
**And** `auth.tenant_identity.verified_by` names the verifier or says
`derived_from_signed_token`
**And** caller-supplied tenant identity is not accepted on the strength of an
internal secret alone

## AC-4: Failure mode is deliberate and tested

**Given** an action depends on portal-api, retrieval-api, a model provider, or
an external connector
**When** the dependency times out or returns 5xx in tests
**Then** the observed behaviour matches `failure.mode`
**And** `fail_open` actions log/telemetry the degradation
**And** `fail_closed` actions return a safe generic error
**And** `fail_loud_degraded` actions explicitly surface the degradation to the
model/user

## AC-5: Model-facing results are capped

**Given** an action returns data to a model host
**When** the upstream returns too many items or oversized text fields
**Then** the action enforces the `result_policy` caps before returning
**And** the returned shape contains only fields allowed by the policy
**And** cross-tenant leakage is covered by either RLS, verified identity,
post-filtering, or a named combination

## AC-6: Telemetry policy is explicit

**Given** an action succeeds
**When** telemetry is inspected
**Then** all events listed in `telemetry.events` are emitted or deliberately
fire-and-forget
**And** telemetry failure does not change the action result unless the
ActionSpec explicitly says telemetry is part of the critical path
**And** privacy-sensitive raw content is gated by the action's telemetry/data
policy

## AC-7: Legacy actions are not forced into a mass migration

**Given** an existing action is untouched by a PR
**When** CI runs after SPEC-ACTION-CONTRACT-001 phase 1
**Then** CI does not fail solely because that legacy action lacks ActionSpec
metadata
**And** CI may fail when the PR materially changes that action without adding
or updating the metadata

## AC-8: Review can find action contracts by action_id

**Given** an engineer searches the repo for an `action_id`
**When** they search for e.g. `knowledge-mcp.search_knowledge`
**Then** they find the metadata block or owning SPEC
**And** the block points to the entrypoint and relevant tests

## AC-9: No production behaviour changes from adopting the convention

**Given** phase 1 lands validation helpers and metadata for one pilot action
**When** the service is deployed
**Then** request/response behaviour, auth decisions, queue execution, and
telemetry payloads remain unchanged except for optional metadata-only logs if
explicitly added

## AC-10: First pilot backfill is reviewable

**Given** `search_knowledge` or the LiteLLM retrieval call is chosen as the
first pilot
**When** its ActionSpec is added
**Then** the diff is small enough to review without re-reading unrelated hook
logic
**And** existing tests for that action still pass
**And** the metadata captures the existing behaviour rather than changing it

