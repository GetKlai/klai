---
id: SPEC-SEC-ENVFILE-SCOPE-001
version: 0.2.0
status: draft
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
priority: high
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-ENVFILE-SCOPE-001: Replace `env_file: .env` with Explicit Per-Service Environment

## HISTORY

### v0.2.0 (2026-04-24)
- Expanded stub to full EARS SPEC after repo-wide audit of
  `deploy/docker-compose.yml`
- Full audit confirmed the stub assumption: **every service that declares
  `env_file: .env`** (portal-api, victorialogs, retrieval-api, scribe-api)
  receives every secret from the shared `/opt/klai/.env` into its process
  environment. The original scribe finding was not an outlier.
- Three services currently use a per-service `env_file: <service>/.env`
  (klai-mailer, librechat-getklai, klai-connector). Those already match
  the target pattern and only need their shared `.env` loaded vars
  audited.
- All other Klai services already use an explicit `environment:` block
  with no `env_file:` at all — they serve as the reference shape.
- Added REQ-1..REQ-6 with sub-requirements, expanded threat model, CI
  gate details, and a staged rollout sequence that moves one service per
  deploy window.

### v0.1.0 (2026-04-24)
- Stub created from internal-audit wave on klai-scribe
- Priority P1 — blast-radius multiplier for any RCE in any service
- Expand via `/moai plan SPEC-SEC-ENVFILE-SCOPE-001`

---

## Goal

Every Docker service SHALL have an explicit `environment:` block listing
only the env vars that service actually reads. `env_file: .env`
(pointing at the shared global `/opt/klai/.env`) SHALL be removed from
every service definition. Blast radius of any single-service RCE SHALL
be bounded to that service's declared secret set, so that a scribe-api
compromise cannot exfiltrate portal-api's `INTERNAL_SECRET`,
`ENCRYPTION_KEY`, `ZITADEL_PAT`, Moneybird webhook token, etc.

---

## Success Criteria

- No service in `deploy/docker-compose.yml` uses `env_file: .env`
  (the shared global file). Per-service `env_file: ./<service>/.env`
  pointing at a SOPS-owned private file is acceptable (REQ-6).
- Each service has an explicit `environment:` block enumerating
  required env vars derived from its own pydantic-settings /
  `os.getenv` call sites.
- A new `deploy/SECRETS_MATRIX.md` documents which service legitimately
  needs which secret, with a one-line rationale per row.
- After deploy, `docker exec <ctr> printenv | grep -iE
  'secret|password|key|token'` returns only the declared secrets for
  that container — no surprise inheritance.
- A CI check blocks any new `env_file: .env` line from being
  reintroduced in `deploy/docker-compose.yml`.
- Regression smoke: `docker exec klai-core-scribe-api-1 printenv
  PORTAL_API_INTERNAL_SECRET` returns empty. Same for `ENCRYPTION_KEY`,
  `ZITADEL_PAT`, `MONEYBIRD_WEBHOOK_TOKEN`, `VEXA_WEBHOOK_SECRET`,
  `PORTAL_API_PORTAL_SECRETS_KEY`.

---

## Environment

- **File primarily affected:** [deploy/docker-compose.yml](../../../deploy/docker-compose.yml) (1252 lines, 60+ services)
- **Supporting artifacts:**
  - [klai-infra/core-01/.env.sops](../../../klai-infra/core-01/.env.sops) — shared global SOPS file (rendered to `/opt/klai/.env` by `deploy.sh`)
  - Per-service SOPS files: `klai-infra/core-01/{caddy,litellm,zitadel,klai-mailer}/.env.sops`
  - Services with their own `.env`-in-repo file: `deploy/klai-mailer/.env`, `deploy/klai-connector/.env`, `deploy/librechat/getklai/.env`
- **New file created:** `deploy/SECRETS_MATRIX.md`
- **CI workflow modified:** `.github/workflows/` — new `env-scope-guard.yml` job OR inline step in an existing workflow
- **Rule coupling:**
  - [.claude/rules/klai/infra/sops-env.md](../../../.claude/rules/klai/infra/sops-env.md) — SOPS procedure stays unchanged
  - [.claude/rules/klai/lang/docker.md](../../../.claude/rules/klai/lang/docker.md) — already notes "portal-api uses explicit `environment:` block — env vars are NOT auto-forwarded from `.env`"
  - [.claude/rules/klai/pitfalls/process-rules.md](../../../.claude/rules/klai/pitfalls/process-rules.md) — add `shared-env-file-pattern` entry via `/klai:retro` after landing

## Assumptions

- Each service has a relatively stable required-secret list; once a
  one-time audit sets the explicit block, roster changes are quarterly
  at most. A CI guard (REQ-4) prevents drift by blocking silent
  reintroduction of `env_file: .env`.
- SOPS is the single source of truth for every env var value. Removing
  `env_file: .env` does NOT remove any var from SOPS; it only stops a
  given service from **inheriting** vars it does not declare. All
  values continue to flow through `deploy.sh main` → `/opt/klai/.env`
  → Docker Compose interpolation of `${VAR}` in explicit
  `environment:` entries.
- Services that currently legitimately need a shared secret
  (e.g. `POSTGRES_PASSWORD`) will continue to receive it through
  explicit declaration, not inheritance.
- The deploy pipeline (`deploy.sh` + GitHub Action merge) does NOT need
  any changes for REQ-1–REQ-4. REQ-6 (per-service SOPS wiring) is the
  only change that interacts with the pipeline, and only for services
  that currently route their own `.env` via a committed `deploy/<svc>/.env`
  (klai-mailer, klai-connector, librechat-getklai).
- Docker Compose interpolation (`${VAR}`) continues to resolve against
  `/opt/klai/.env` at `docker compose up` time. This does NOT leak the
  unreferenced variables into the container — interpolation only
  substitutes variables the compose file explicitly references.

---

## Out of Scope

- Replacing SOPS with HashiCorp Vault / external secret manager — future infra SPEC.
- Renaming any env vars — renames are high-risk and binary-incompatible with current callers. Names stay exactly as-is.
- Per-service secret rotation cadence — owned by [SPEC-SEC-005](../SPEC-SEC-005/spec.md) rotation runbook.
- Changing the SOPS workflow or `deploy.sh` merge behaviour. SOPS remains the source of truth; this SPEC only narrows which container sees which variable at runtime.
- Auditing / reducing the shared secret inventory itself (e.g. merging `PORTAL_API_INTERNAL_SECRET` and `DOCS_INTERNAL_SECRET`). Consolidation is a separate governance call.
- Secrets managed inside images (e.g. baked-in Trivy DB) — only runtime env-var scope is in scope.
- klai-focus/research-api — FROZEN per its README and not present in `docker-compose.yml`. Any future re-enablement pre-requisites an explicit `environment:` block.
- Moving values currently hardcoded in compose (`QDRANT_URL: http://qdrant:6333`) out of the compose file. Non-secret defaults remain inline.

---

## Security Findings Addressed

- **Internal wave — scribe env_file** (2026-04-24, P1): scribe-api uses
  `env_file: .env` pulling every host secret into its process environment,
  including `PORTAL_API_INTERNAL_SECRET`, `ENCRYPTION_KEY`, `ZITADEL_PAT`,
  `PORTAL_SECRETS_KEY`, `VEXA_WEBHOOK_SECRET`, `MONEYBIRD_WEBHOOK_TOKEN`.
  Medium severity in isolation; CRITICAL chain when combined with any
  RCE primitive in scribe (see SPEC-SEC-MAILER-INJECTION-001 for an
  adjacent RCE class on klai-net).
- **Internal wave — pattern repeat**: the same `env_file: .env` anti-pattern
  is present on portal-api (docker-compose.yml:322), victorialogs (:479),
  retrieval-api (:635), scribe-api (:758). All four are in scope of this
  SPEC.
- **Tracker reference:** [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md) §"New P1 findings → new SPECs" — "SPEC-SEC-ENVFILE-SCOPE-001".

---

## Threat Model

Primary threat mitigated: **cross-service secret compromise from a
single-service RCE**. Today any of the four services using
`env_file: .env` inherits the entire contents of `/opt/klai/.env` into
its process environment. A Python RCE in any one of them (untrusted
template rendering, deserialisation, image parsing) lets the attacker
dump `os.environ` and walk away with every Klai secret.

Adversary scenarios considered:

1. **RCE in scribe-api → portal tenant takeover.** Attacker uploads a
   crafted audio file that triggers an RCE in a transcription pipeline
   library. Today: attacker reads `PORTAL_API_INTERNAL_SECRET` from
   `os.environ`, calls portal-api `/internal/v1/...` to mutate any
   tenant, then reads `ENCRYPTION_KEY` to decrypt stored credentials.
   After this SPEC: scribe's process env has only scribe's own secrets
   (`POSTGRES_PASSWORD`, `LITELLM_MASTER_KEY`, `KNOWLEDGE_INGEST_SECRET`,
   Zitadel issuer). Cross-service compromise requires a second vulnerability.
2. **RCE in retrieval-api → Moneybird webhook abuse.** Attacker
   exploits a query-crafting path in retrieval-api. Today:
   `MONEYBIRD_WEBHOOK_TOKEN`, `VEXA_WEBHOOK_SECRET`, Moneybird product
   IDs are all visible in the process env — attacker forges a webhook
   to portal-api and upgrades a tenant's plan. After this SPEC:
   retrieval-api's process env contains only its own secrets
   (`INTERNAL_SECRET`, `QDRANT_API_KEY`, `LITELLM_API_KEY`,
   `REDIS_URL`, `ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE`,
   `POSTGRES_PASSWORD`). Moneybird is unreachable.
3. **Supply-chain compromise of a library used by portal-api.** A
   transitive dependency ships a malicious update that exfiltrates
   `os.environ`. Today: one compromised process, every secret
   (including mailer, scribe, retrieval API keys) leaks. After this
   SPEC: portal-api still sees a lot (it's the orchestrator), but it
   no longer sees `VICTORIALOGS_AUTH_PASSWORD`, `GARAGE_SECRET_KEY`,
   `CRAWL4AI_INTERNAL_KEY`, etc. — only secrets portal-api actually
   uses.
4. **Accidental log leak.** A debug handler dumps `os.environ`. Today:
   every secret on the host ends up in VictoriaLogs. After this SPEC:
   only the service's own declared secrets leak. Less bad,
   recoverable with a narrower rotation.

Explicit non-goals:
- Defeating a full host compromise (root on core-01). Anyone with root
  reads `/opt/klai/.env` directly — env-var scoping does not help
  there. Host hardening lives elsewhere.
- Protecting against a compromise of a service that legitimately owns
  a secret. Scribe still needs its database password; if scribe is
  compromised, the attacker has its database password. The protection
  is against **cross-service** compromise.

Blast-radius reduction summary: a single-service RCE changes from
"every Klai secret on the host" to "only the secrets that service was
designed to hold", which is the Defence-in-Depth minimum reasonable
posture for a shared `.env`-based stack that is not yet on Vault.

---

## Requirements

### REQ-1: Explicit `environment:` Block Per Service

Every service in `deploy/docker-compose.yml` SHALL enumerate its
required env vars via an `environment:` block. `env_file: .env`
(pointing at the shared global file) SHALL NOT appear on any service
definition.

- **REQ-1.1:** WHEN `deploy/docker-compose.yml` is read, THE file
  SHALL NOT contain the substring `env_file: .env` (bare, shared-global
  form) on any service. Per-service forms such as
  `env_file: ./<service>/.env` are permitted where REQ-6 applies, but
  the bare `.env` at repo root is forbidden.
- **REQ-1.2:** WHEN any service currently using `env_file: .env` is
  migrated, THE migration SHALL add an explicit `environment:` block
  containing only the env vars that service's source reads (derived
  from the audit in research.md).
- **REQ-1.3:** WHEN the migration replaces `env_file: .env`, IF the
  service already has an `environment:` block, THE existing block
  SHALL be extended with the previously-inherited vars rather than
  replaced. Keys already present SHALL keep their current values
  verbatim (no behaviour change on current declared vars).
- **REQ-1.4:** WHEN a service no longer needs `env_file: .env`, THE
  corresponding line in docker-compose.yml SHALL be removed in the
  same commit that adds the explicit block — never in a follow-up
  commit — to prevent a window where the service boots without its
  secrets.
- **REQ-1.5:** WHERE a service legitimately needs a large shared block
  (e.g. portal-api reads >30 secrets), THE block SHALL be sorted
  alphabetically by key within logical groups, with inline comments
  grouping related secrets (database, auth, vendor webhooks) for
  reviewability. The existing portal-api block already follows this
  style; new migrations match it.

### REQ-2: SECRETS_MATRIX.md Documentation

A new file `deploy/SECRETS_MATRIX.md` SHALL be created and kept in
lock-step with `deploy/docker-compose.yml`. It maps each env-var key
to every service that legitimately reads it, with a one-line rationale
per row.

- **REQ-2.1:** WHEN `deploy/SECRETS_MATRIX.md` is read, THE document
  SHALL contain a table of `| Secret | Service | Purpose |` rows,
  sorted by secret name, then by service.
- **REQ-2.2:** WHEN a new env var is added to any service's
  `environment:` block, THE matrix SHALL be updated in the same PR
  with a new row (enforced by the Rule 2 decomposition protocol and
  reviewer checklist).
- **REQ-2.3:** WHEN a service's source stops reading an env var, THE
  matrix row SHALL be removed in the same PR that removes the env var
  from the `environment:` block.
- **REQ-2.4:** WHERE a secret is widely shared (e.g. `POSTGRES_PASSWORD`
  used by 8+ services), THE matrix SHALL list every consumer explicitly
  rather than using "all services" shorthand.
- **REQ-2.5:** THE matrix MAY group related secrets with a short
  leading paragraph (e.g. "PostgreSQL credentials", "Vexa webhook
  tokens") for human scanability. The authoritative row-per-secret
  table remains the primary artifact.

### REQ-3: Runtime `printenv` Verification

After deploy, the runtime environment of each container SHALL contain
only the env vars that container's `environment:` block declares. No
surprise inheritance from the shared `.env`.

- **REQ-3.1:** WHEN `docker exec <container> printenv` is executed
  on any container that was migrated by this SPEC, THE output
  SHALL contain only the keys declared in that service's
  `environment:` block, plus Docker-framework keys (`PATH`, `HOME`,
  `HOSTNAME`, `LANG`, and framework keys set by the base image).
- **REQ-3.2:** WHEN the regression-smoke check `docker exec
  klai-core-scribe-api-1 printenv | grep -iE
  'PORTAL_API_INTERNAL_SECRET|ENCRYPTION_KEY|ZITADEL_PAT|
  MONEYBIRD_WEBHOOK_TOKEN|VEXA_WEBHOOK_SECRET|
  PORTAL_API_PORTAL_SECRETS_KEY'` is run, THE output SHALL be empty.
- **REQ-3.3:** WHEN the same check is run against any other service
  migrated by this SPEC (portal-api, victorialogs, retrieval-api),
  THE output SHALL contain only the subset of those keys that service
  legitimately owns. Example: portal-api legitimately owns
  `PORTAL_API_INTERNAL_SECRET` (resolves `INTERNAL_SECRET`) — not a
  leak.
- **REQ-3.4:** THE verification SHALL be documented in the rollout
  runbook (research.md §Migration approach) and executed as the final
  gate for each migrated service before moving to the next one.

### REQ-4: CI Gate Against Reintroduction

A GitHub Actions check SHALL fail any PR that reintroduces a bare
`env_file: .env` line to `deploy/docker-compose.yml`.

- **REQ-4.1:** WHEN a PR is opened or updated AND the diff touches
  `deploy/docker-compose.yml`, THE CI workflow SHALL execute a grep
  for bare `env_file: .env` (and the multi-line form) and fail
  the job if any match exists.
- **REQ-4.2:** THE CI check SHALL allow `env_file: ./<service>/.env`
  (per-service forms permitted by REQ-6). The regex matches only the
  bare repo-root `.env` filename with no leading path segment.
- **REQ-4.3:** WHEN the CI check fails, THE failure message SHALL
  reference this SPEC ID (`SPEC-SEC-ENVFILE-SCOPE-001`) and link to
  `deploy/SECRETS_MATRIX.md` so the contributor has a path forward.
- **REQ-4.4:** THE CI check SHALL run on every push to a PR branch,
  not only on the final merge. False positives are cheap to fix; a
  late catch forces an extra commit.
- **REQ-4.5:** THE CI check SHALL use a plain shell grep (not a YAML
  parser) so that a newly-added multi-line `env_file:` with
  ` - .env` on a subsequent line is also caught. The regex pattern
  and exact GitHub Actions YAML live in research.md §CI implementation.

### REQ-5: Staged Rollout — One Service Per Deploy Window

The migration SHALL land service-by-service, each in its own commit
and its own deploy window, with runtime verification between steps.
No big-bang change.

- **REQ-5.1:** THE rollout order SHALL be: scribe-api → retrieval-api
  → victorialogs → portal-api. Rationale: smallest-surface-first; if
  the approach has a flaw it shows up on scribe before it reaches
  portal-api.
- **REQ-5.2:** WHEN a service is migrated, THE commit SHALL touch
  only (a) that service's definition in `docker-compose.yml`, (b) the
  matching rows in `deploy/SECRETS_MATRIX.md`, and optionally (c) the
  per-service SOPS wiring described in REQ-6. No other services in
  the same commit.
- **REQ-5.3:** AFTER each service is deployed to core-01, THE operator
  SHALL run the REQ-3 runtime-printenv verification and record the
  result (minimally a commit-trailer or PR comment) before migrating
  the next service.
- **REQ-5.4:** IF runtime verification shows a missing required var
  (service crashes / logs `KeyError` / health check fails), THE
  operator SHALL roll back the commit immediately (revert + redeploy)
  rather than patching forward. The explicit block must be right
  before the next service's migration starts.
- **REQ-5.5:** WHEN all four services have been migrated and verified,
  THE CI gate (REQ-4) SHALL be activated. Activating the CI gate
  before all four migrations would block the work in progress.

### REQ-6: Per-Service SOPS Wiring (Acceptable Alternative)

For services that already have (or can have) a SOPS-encrypted
per-service `.env.sops` in `klai-infra/core-01/<service>/`, Docker
Compose MAY wire the per-service file via `env_file:
./<service>/.env` instead of listing each var inline. Both patterns
are acceptable; the SPEC requires that **one** of them is used, and
that the choice is documented.

- **REQ-6.1:** WHERE a service has a per-service `.env.sops` in
  `klai-infra/core-01/<service>/.env.sops` (today: caddy, litellm,
  zitadel, klai-mailer), THE service definition MAY use `env_file:
  ./<service>/.env` (relative to `deploy/`) and omit the redundant
  inline `environment:` entries. The deploy pipeline is already
  responsible for decrypting and rendering this file to
  `deploy/<service>/.env`.
- **REQ-6.2:** WHERE a service does NOT have a per-service SOPS file
  (today: portal-api, retrieval-api, scribe-api, victorialogs, and
  every service not listed in REQ-6.1), THE service SHALL use the
  explicit `environment:` block pattern from REQ-1. Creating a new
  per-service SOPS file is out of scope for this SPEC — it is an
  acceptable future refactor.
- **REQ-6.3:** WHEN a service uses the per-service `env_file:`
  pattern, THE `.env.sops` file SHALL contain only the env vars that
  service reads. A per-service SOPS that mirrors the global `.env`
  defeats the purpose of this SPEC and SHALL be considered a bug.
- **REQ-6.4:** WHEN the per-service pattern is used, THE
  SECRETS_MATRIX.md row SHALL cite the per-service `.env.sops` path as
  the source location, so auditors can reach the ciphertext without
  reading compose.
- **REQ-6.5:** THE decision to use the inline `environment:` block vs
  the per-service `env_file:` pattern SHALL be made case-by-case and
  noted in the PR description. Both are permitted; mixing is
  expected (klai-mailer uses per-service today; portal-api will use
  inline).

---

## Non-Functional Requirements

- **Deploy safety:** REQ-5's one-service-at-a-time cadence MUST hold
  even if the operator is tempted to batch. The deployment history
  lives in `git log deploy/docker-compose.yml` and should show one
  service migration per commit.
- **Observability:** Missing-env-var failures SHALL surface in
  VictoriaLogs as `service:<svc> AND level:error AND
  (ValueError OR KeyError)` within the first 60 seconds of deploy.
  The operator is expected to look there after each migration.
- **Rollback cost:** A revert of a single migration commit SHALL
  restore the pre-migration state without data loss. No DB migrations
  or config-file writes outside `docker-compose.yml` + SECRETS_MATRIX.md.
- **Review burden:** Each migration PR SHALL fit in a ~100-line diff
  (one service block + matrix rows). Large blocks like portal-api
  (>30 vars) stay under 200 lines because the `environment:` block
  already exists and is only extended.
- **No behaviour change:** Every migrated service SHALL pass its
  existing smoke flow (health endpoint, representative golden-path
  request) with identical response codes and payloads as before the
  migration.

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A required env var is missed in the audit and a service crashes on boot | MEDIUM | HIGH (service down) | REQ-5 staged rollout + runtime `printenv` + log check before moving on; REQ-5.4 immediate rollback |
| An env var is read dynamically (`os.getenv(name_from_db)`) and can't be static-audited | LOW | MEDIUM | Research audit covers all current dynamic reads; if a new one appears, the CI gate fails the PR because the matrix row is missing |
| CI gate flags `env_file: .env` in a test fixture | LOW | LOW | REQ-4.2 regex scopes to `deploy/docker-compose.yml`; other paths ignored |
| Operator forgets SECRETS_MATRIX.md update in a later PR | MEDIUM | LOW (docs drift) | Add a reviewer-checklist entry; consider an additional CI check that diffs the set of declared env vars against the matrix (future work, not REQ-4 scope) |
| A service legitimately needs a new env var and rollout is mid-flight | LOW | LOW | Standard SOPS-add-then-compose-declare flow from sops-env.md still works; the CI gate only blocks `env_file: .env`, not new explicit declarations |

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Adjacent RCE class: [SPEC-SEC-MAILER-INJECTION-001](../SPEC-SEC-MAILER-INJECTION-001/spec.md) — `str.format(**variables)` RCE is the concrete exploit primitive that makes today's blast radius unacceptable
- Identity assertion: [SPEC-SEC-IDENTITY-ASSERT-001](../SPEC-SEC-IDENTITY-ASSERT-001/spec.md) — complementary defence-in-depth on the verification side of internal calls
- Rotation runbook: [SPEC-SEC-005](../SPEC-SEC-005/spec.md) — narrower per-service secret scope means a rotation of one secret touches fewer containers; update its consumer list after this SPEC lands
- SOPS rules: [.claude/rules/klai/infra/sops-env.md](../../../.claude/rules/klai/infra/sops-env.md) — unchanged by this SPEC
- Docker rules: [.claude/rules/klai/lang/docker.md](../../../.claude/rules/klai/lang/docker.md) — already encodes the portal-api "explicit environment" pattern that REQ-1 generalises
