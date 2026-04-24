# Acceptance Criteria — SPEC-SEC-ENVFILE-SCOPE-001

EARS-format acceptance tests that MUST pass before
SPEC-SEC-ENVFILE-SCOPE-001 is considered complete. Verification
happens against `deploy/docker-compose.yml`,
`deploy/SECRETS_MATRIX.md`, the GitHub Actions workflow, and the
running containers on core-01.

## AC-1: scribe-api is scoped to scribe-only secrets

- **WHEN** the scribe-api container is deployed with the migrated
  compose file **AND** an operator runs `ssh core-01 "docker exec
  klai-core-scribe-api-1 printenv"` **THE** output **SHALL** contain
  these keys and no other Klai secrets: `POSTGRES_DSN`,
  `WHISPER_SERVER_URL`, `ZITADEL_ISSUER`, `LITELLM_BASE_URL`,
  `LITELLM_MASTER_KEY`, `KNOWLEDGE_INGEST_URL`,
  `KNOWLEDGE_INGEST_SECRET`.
- **WHEN** the same command pipe is filtered with `| grep -iE
  'PORTAL_API_INTERNAL_SECRET|ENCRYPTION_KEY|ZITADEL_PAT|
  PORTAL_API_PORTAL_SECRETS_KEY|VEXA_WEBHOOK_SECRET|
  MONEYBIRD_WEBHOOK_TOKEN'` **THE** output **SHALL** be empty. This is
  the headline smoking-gun regression from the internal audit.
- **WHEN** the scribe-api container is restarted post-migration **THE**
  service **SHALL** pass its existing health check and accept a
  representative upload on the golden-path flow (signed URL → upload
  → transcription request → status `done`).

## AC-2: retrieval-api is scoped to retrieval-only secrets

- **WHEN** `ssh core-01 "docker exec klai-core-retrieval-api-1
  printenv"` is run post-migration **THE** output **SHALL NOT**
  contain `PORTAL_API_INTERNAL_SECRET`, `ENCRYPTION_KEY`,
  `MONEYBIRD_*`, `VEXA_*`, `GLITCHTIP_*`, or any `KUMA_TOKEN_*`.
- **WHEN** a `/retrieve` request is sent with a valid
  `X-Internal-Secret` header **THE** service **SHALL** return its
  normal response (search results) within the existing SLO. No
  functional regression.

## AC-3: portal-api is scoped to portal-declared secrets

- **WHEN** `ssh core-01 "docker exec klai-core-portal-api-1
  printenv"` is run post-migration **THE** output **SHALL NOT**
  contain `VICTORIALOGS_AUTH_PASSWORD`, `GRAFANA_CADDY_HASH`,
  `HETZNER_AUTH_API_TOKEN`, Vexa-specific secrets
  (`VEXA_DB_PASSWORD`, `VEXA_REDIS_PASSWORD`,
  `WHISPER_SERVICE_TOKEN`, `BOT_API_TOKEN`, `INTERNAL_API_SECRET`),
  GlitchTip secrets, Gitea admin tokens, Searxng secret, or any
  `KUMA_TOKEN_*`.
- **WHEN** portal-api's golden flow is exercised (login at
  `https://my.getklai.com` → portal dashboard loads → internal API
  calls to knowledge-ingest succeed) **THE** behaviour **SHALL** be
  identical to pre-migration.
- **WHEN** any feature currently depending on an env var that was
  silent-inherited pre-migration is exercised, **IT** **SHALL**
  continue to work because the migration PR added every code-referenced
  env var to the explicit block. (If AC-3 fails on a specific feature
  that is the signal the audit missed a var; REQ-5.4 rollback applies.)

## AC-4: victorialogs is scoped to its two auth env vars

- **WHEN** `ssh core-01 "docker exec klai-core-victorialogs-1
  printenv"` is run post-migration **THE** output **SHALL** contain
  `VICTORIALOGS_AUTH_USER` and `VICTORIALOGS_AUTH_PASSWORD` and
  **SHALL NOT** contain any other Klai secret.
- **WHEN** the VictoriaLogs healthcheck runs **THE** probe **SHALL**
  succeed — the `$$VICTORIALOGS_AUTH_*` interpolation inside the
  container shell still resolves correctly.
- **WHEN** the VictoriaLogs MCP tunnel is used from a Mac laptop
  **THE** queries **SHALL** still work (the external auth Bearer
  token is handled by Caddy, not by the VictoriaLogs container env).

## AC-5: No `env_file: .env` in docker-compose.yml

- **WHEN** `grep -nE '^\s*env_file:\s*\.env\s*$'
  deploy/docker-compose.yml` is run on the post-SPEC tree **THE**
  output **SHALL** be empty.
- **WHEN** the multi-line form `grep -nzE
  'env_file:\s*\n\s*-\s*\.env\s*\n' deploy/docker-compose.yml` is
  run **THE** output **SHALL** be empty.
- **WHEN** `grep -nE 'env_file:' deploy/docker-compose.yml` is run
  **THE** output **SHALL** show only per-service paths (e.g.
  `./klai-mailer/.env`, `./klai-connector/.env`,
  `./librechat/getklai/.env`) and nothing of the bare `.env` form.

## AC-6: SECRETS_MATRIX.md exists and is in lock-step

- **WHEN** an operator opens `deploy/SECRETS_MATRIX.md` **THE** file
  **SHALL** exist and contain a sorted table `| Secret | Service |
  Purpose |` covering every env var declared under any
  `environment:` block in `deploy/docker-compose.yml`.
- **WHEN** a diff is run `comm -23 <(grep -oE '\$\{[A-Z_][A-Z0-9_]*\}'
  deploy/docker-compose.yml | sort -u) <(awk -F'|' 'NR>2 {print
  "${"$2"}"}' deploy/SECRETS_MATRIX.md | sort -u)` (informal lint —
  exact invocation documented in the runbook) **THE** difference
  **SHOULD** be empty, modulo a known-acceptable set (non-secret
  values like `DOMAIN`, `LOG_LEVEL`, ports).
- **WHEN** a PR introduces a new env var to any service, **THE**
  reviewer **SHALL** reject the PR unless the matching row is added
  to `deploy/SECRETS_MATRIX.md`. The reviewer checklist captures
  this; automated lint is a future improvement.

## AC-7: CI blocks new `env_file: .env` lines

- **WHEN** a PR adds `env_file: .env` to `deploy/docker-compose.yml`
  **THE** `env-scope-guard` workflow **SHALL** fail with an error
  message referencing `SPEC-SEC-ENVFILE-SCOPE-001` and linking to
  `deploy/SECRETS_MATRIX.md`.
- **WHEN** a PR adds `env_file: ./klai-mailer/.env` (a per-service
  path) to `deploy/docker-compose.yml` **THE** workflow **SHALL NOT**
  fail — per-service paths are permitted by REQ-6.
- **WHEN** a PR does not touch `deploy/docker-compose.yml` **THE**
  workflow **SHALL NOT** run (paths filter).
- **WHEN** the workflow runs **IT** **SHALL** complete within 10
  seconds (grep-only; no Python, Node, or Docker).
- **WHEN** the workflow is activated (per REQ-5.5), **IT** **SHALL**
  be after all four service migrations have landed, not before.

## AC-8: Rollout is staged — one service per commit

- **WHEN** `git log -- deploy/docker-compose.yml` is inspected post-SPEC
  **THE** history **SHALL** show four distinct migration commits, one
  per service, in the order: scribe-api, retrieval-api, victorialogs,
  portal-api (per REQ-5.1).
- **WHEN** any migration commit is reviewed in isolation **THE** diff
  **SHALL** touch only `deploy/docker-compose.yml` (that service's
  block) and `deploy/SECRETS_MATRIX.md` (rows for that service). No
  other files (modulo test-scaffolding changes in
  `.github/workflows/` for the final activation commit).
- **WHEN** a migration commit is reverted **THE** pre-migration
  behaviour **SHALL** be fully restored without data loss.

## AC-9: Runtime verification runbook executed per service

- **WHEN** a service is migrated in production **THE** operator
  **SHALL** record (in the PR comment thread or commit trailer) the
  output of the following sequence, proving runtime correctness:

  1. `ssh core-01 "docker compose -f /opt/klai/docker-compose.yml up
     -d <svc>"` (expected: container recreated)
  2. `ssh core-01 "docker logs --tail 30 klai-core-<svc>-1"`
     (expected: clean startup, no `KeyError` / `ValueError` /
     `AttributeError` on boot)
  3. `ssh core-01 "docker ps --filter name=<svc> --format
     '{{.Names}}\t{{.Status}}'"` (expected: `Up X seconds` or
     `healthy`)
  4. `ssh core-01 "docker exec klai-core-<svc>-1 printenv | wc -l"`
     (expected: a noticeably smaller number than before — e.g.
     scribe-api goes from ~130+ lines to ~20)
  5. `ssh core-01 "docker exec klai-core-<svc>-1 printenv | grep -iE
     'secret|password|key|token'"` (expected: contains only the
     service's own declared secrets)
  6. Smoke test of one golden-path request against the service
     (expected: 2xx, correct payload)

- **WHEN** any step fails **THE** migration commit **SHALL** be
  reverted before the next service is migrated.

## AC-10: Every service still boots and serves traffic

- **WHEN** the entire rollout is complete **THE** following services
  **SHALL** report `Up` / `healthy` via `docker ps`:
  portal-api, scribe-api, retrieval-api, victorialogs.
- **WHEN** the portal login flow is exercised at
  `https://my.getklai.com` **THE** user **SHALL** successfully sign
  in via Zitadel and reach the portal dashboard.
- **WHEN** a knowledge-retrieve chat turn is exercised in
  LibreChat **THE** LiteLLM hook **SHALL** successfully call
  retrieval-api and return results.
- **WHEN** a scribe-api transcription is triggered (e.g. upload an
  audio test file through the meetings flow) **THE** pipeline
  **SHALL** produce a transcript within the usual SLO.
- **WHEN** a VictoriaLogs LogsQL query is issued from Grafana **THE**
  response **SHALL** match pre-migration behaviour.

## AC-11: Rollback procedure is documented and tested

- **WHEN** a migration PR is merged **THE** PR description **SHALL**
  include a "Rollback" section with the exact `git revert`
  invocation and the post-revert `docker compose up -d <svc>`
  command. Review gate: reviewer checks for the section.
- **WHEN** a rollback is exercised on a staging or core-01 replica
  during acceptance testing (optional but recommended for the
  portal-api migration) **THE** service **SHALL** return to the
  `env_file: .env` state within one deploy window.

## AC-12: Per-service `env_file:` paths remain working

- **WHEN** `docker compose config klai-mailer | yq
  '.services.klai-mailer.env_file'` is run (or the equivalent
  manual inspection) **THE** output **SHALL** still resolve to
  `./klai-mailer/.env`.
- **WHEN** klai-mailer, klai-connector, and librechat-getklai are
  restarted post-SPEC **THEY** **SHALL** continue to load their
  per-service `.env` file without change. This SPEC does not modify
  their env wiring.
- **WHEN** `deploy/klai-mailer/.env` is audited (REQ-6.3 informal
  review) **THE** file **SHOULD** contain only mailer-relevant env
  vars. If it mirrors the global `.env`, a follow-up issue is filed
  — NOT blocking this SPEC.

## AC-13: No SOPS procedure regression

- **WHEN** a SOPS change is made to `klai-infra/core-01/.env.sops`
  using the procedure in `.claude/rules/klai/infra/sops-env.md` and
  pushed to main **THE** GitHub Action **SHALL** still render the
  merged file to `/opt/klai/.env` exactly as before. No deploy
  pipeline change is expected from this SPEC.
- **WHEN** `docker compose up -d <svc>` is run on core-01 for any
  migrated service **THE** container **SHALL** receive the updated
  values via `${VAR}` interpolation, NOT via `env_file: .env`
  inheritance.

## AC-14: SPEC-SEC-005 consumer list stays in sync

- **WHEN** `klai-infra/INTERNAL_SECRET_ROTATION.md` (owned by
  [SPEC-SEC-005](../SPEC-SEC-005/spec.md)) is reviewed after this
  SPEC lands **THE** consumer list for `INTERNAL_SECRET` **SHALL**
  continue to match the services that now explicitly declare the
  variable (portal-api, knowledge-ingest, retrieval-api, connector,
  scribe-api, mailer, plus LibreChat patch / LiteLLM hook entries if
  they apply).
- **WHEN** a service is removed from receiving `INTERNAL_SECRET` as a
  result of the migration **THE** rotation runbook **SHALL** drop
  that service in the same PR (a cross-SPEC edit, coordinated during
  implementation).

## AC-15: Confidence gate

- **WHEN** the implementation completion message is posted it
  **SHALL** end with `Confidence: [0-100] — [evidence summary]` per
  `.claude/rules/klai/pitfalls/process-rules.md` rule
  `report-confidence`. Only observable evidence counts:
  - AC-1..AC-4 printenv outputs captured in the PR thread
  - AC-7 CI workflow run link (failed run for the guard, successful
    run for a legitimate PR)
  - AC-10 golden-flow smoke screenshots or curl outputs
