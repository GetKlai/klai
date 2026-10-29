---
id: SPEC-INFRA-AUTO-ROLLBACK-001
version: "0.1.0"
status: draft
created: 2026-10-28
updated: 2026-10-28
author: Claude (Opus), commissioned by Mark Vletter
priority: high
related:
  - SPEC-INFRA-CONTAINER-HYGIENE-001 (compose-up.sh is the single recreate wrapper)
  - SPEC-INFRA-COOLIFY-EXIT-001 T2 (Twenty one-minor-at-a-time upgrades)
  - SPEC-INFRA-005 (deploy/volume-mounts.yaml, the stateful volume inventory)
  - SPEC-CI-TRIVY-POLICY-001 (scan gates the deploy)
  - renovate.json5 (update policy this spec changes in section 7)
---

# HISTORY

| Version | Date       | Author | Change |
|---------|------------|--------|--------|
| 0.1.0   | 2026-10-28 | Claude (Opus), commissioned by Mark Vletter | First draft, built on the current deploy workflows and two field-practice research passes. |

---

# SPEC-INFRA-AUTO-ROLLBACK-001: deploy automatically, roll back automatically

## 1. Goal

Every update (patch, minor, major; ours or a dependency's) goes live without a
human step. When it breaks, the previous working state comes back without a
human step. Humans are informed, not asked.

Today half of that exists. Renovate automerges patches and minors after a
7-day release age, every service workflow deploys to core-01 on a push to
`main`, `compose-up.sh` refuses to call a crash-looping container a success,
`E2E prod-tenant` walks the customer journeys after each deploy, and
`ci-failure-notice.yml` opens an issue on a red main run. What does not exist
anywhere is the second half: no script restores a previous image, compose file,
config or data snapshot. A red deploy today leaves production broken until
someone acts.

### 1.1 Non-goals

- **The AI fixer.** A later Claude Code GitHub Action may open a fix PR after a
  rollback. This spec only defines the hook it needs (section 9).
- **OS rollback.** core-01 and public-01 run Ubuntu with unattended-upgrades.
  Ubuntu has no atomic rollback; only image-based systems (Flatcar, Fedora
  CoreOS with greenboot) do. Moving hosts is a migration of its own, not part of
  this change. We keep Ubuntu and add an alert for a pending or failed reboot
  (open decision D4).
- **The release-age gap for large Docker Hub repositories.** The 7-day
  `minimumReleaseAge` is not enforced for Docker Hub images with more than 1000
  tags (mongo, postgres, redis, rabbitmq, meilisearch, grafana, ollama, node,
  python, nvidia/cuda). A separate fix is in progress. This spec depends on it
  and does not solve it: automatic rollback is a second line, not a reason to
  drop the cooldown.
- **Rolling back data or migrations by running them in reverse.** Nothing here
  runs a down-migration. See section 3.

## 2. Field practice this builds on

Two research passes on 2026-10-28 found one pattern used without Kubernetes
(Nomad `auto_revert`, Kamal, docker-rollout, hand-written Actions jobs):
record the running image digest, deploy, run a smoke test, and on failure
redeploy the recorded digest and fail the run loudly. That pattern never rolls
back data, so schema changes must be expand-then-contract: the previous image
must still run on the new schema.

Stateful engines (Postgres after `pg_upgrade --link`, Qdrant, Meilisearch,
Mongo after a featureCompatibilityVersion bump) cannot downgrade in place. The
only rollback is a snapshot taken immediately before the change. Minors can be
automated as snapshot, health gate, restore on red; Postgres majors stay a
scripted, human-approved operation on a copy.

Supply chain: pin GitHub Actions by commit SHA, keep the cooldown, let security
fixes bypass it. For automation chains, the most reported failure is silence
(an expired token, a cron that stopped), which is why section 8 adds a
dead-man's switch.

## 3. Rules that make rollback safe

R1. **Deploy exact artifacts.** A deploy pulls `ghcr.io/getklai/<svc>:<sha>`,
not `:latest`. Rollback needs to name what it is going back to.

R2. **Record before you change.** Before a recreate, the image ID currently
running and the config files being replaced are kept on the host (a
`klai/<svc>:previous` tag; `*.prev` copies of compose and config files) and one
line is appended to a deploy ledger on core-01 (service, previous, new, commit,
time, kind `image|compose|data`).

R3. **Expand, then contract.** A migration that removes or renames a table or
column ships in a PR that changes no application code, after the release that
stopped using it is live. Additive migrations ship with the code. Rollback
never runs `alembic downgrade` and never skips `post_deploy_*.sql`; the previous
image runs on the newer schema. Known ceiling: the CI check (slice S2) can see
that a contract PR has no code changes, not that the previous release is
already live; the second half stays a review rule.

R4. **Data rollback is a restore, and only inside a window.** A data store is
rolled back by restoring the snapshot taken just before the change. That loses
every write after the snapshot, so it happens automatically only within 30
minutes of the new version starting. After that a red gate opens an issue and
nothing is restored without a human.

R5. **Fail loudly, in two flavours.** A rolled-back deploy ends red with
outcome `rolled_back` (exit 3 from the rollback script). A rollback that itself
failed ends red with outcome `rollback_failed` (exit 4). The second is the only
case that needs a human now.

R6. **One rollback per change.** If the restored state also fails its gate, the
script stops and reports `rollback_failed`. It never loops.

## 4. Contract per layer

Each layer answers: what "broken" means, what is rolled back, what cannot be,
who hears about it, and how the rollback itself is proven.

### 4.a Stateless services built from this repo

Scope: every service whose `Build and push <svc>` workflow deploys to core-01
(portal-api, retrieval-api, knowledge-ingest, klai-connector, klai-mailer,
klai-slack, klai-knowledge-mcp, scribe-api, klai-docker-authz, caddy, portal
frontend, docs, LiteLLM hooks, LibreChat base config).

- **Broken means**, in order: (1) `compose-up.sh` verification fails (not
  running, restarted during verification, healthcheck unhealthy); (2) the
  service's HTTP health probe from inside core-01 does not answer 2xx within 60
  seconds; (3) the next `E2E prod-tenant` run triggered by this deploy fails
  (section 6); (4) later, an error-rate breach in the 15 minutes after the
  deploy (slice S11).
- **Rolled back:** the image. Gates 1 and 2 roll back inside the deploy job by
  retagging `klai/<svc>:previous` to the running tag and calling `compose-up.sh`
  again. Gate 3 rolls back from a separate workflow using the ledger.
- **Cannot be rolled back:** schema changes and `post_deploy_*.sql` already
  applied by `deploy-portal-api.sh`, and any data the new version wrote. R3 makes
  that safe.
- **Informed:** the deploy run fails, `ci-failure-notice.yml` opens or updates
  its issue and adds label `auto-rollback` (or `rollback-failed`) with the
  report; the rollback script logs one `auto_rollback` line that a Grafana rule
  turns into mail through the existing contact points.
- **Proven:** the weekly rehearsal (section 8) deploys a deliberately broken
  image to a canary service through the same script.

### 4.b Third-party images and config in deploy/docker-compose.yml

Scope: what `deploy-compose.yml` syncs (compose files, Caddyfile, Alloy and
SearXNG config, Grafana provisioning, LibreChat entrypoints, scripts) and every
image bump Renovate merges into the compose file.

- **Broken means:** a recreate failing `compose-up.sh` verification inside
  `compose-env-drift-recreate.sh` or `sync_and_recreate`, a failing
  `check-twenty-upgrade.sh`, and a failing E2E run after the sync. The socket
  proxy smoke test stays non-blocking as today.
- **Rolled back:** the previous compose files and config files (`*.prev`, kept
  by the job before it overwrites), then the same recreate path, then the same
  verification.
- **Cannot be rolled back:** images that migrate their own database on start.
  Verified examples: Twenty (`upgrade` on start), LiteLLM with
  `USE_PRISMA_MIGRATE`, GlitchTip (`manage.py migrate`), Zitadel
  (`start-from-init`). Rolling those back by image alone can leave the old
  version on a newer schema it does not understand. They are treated as layer
  (c): their database is snapshotted before the bump and restored with the
  image. Until slice S8 lands, a red gate for these images opens a
  `rollback-failed` issue instead of reverting the image alone.
- **Main must not keep the bad version.** After a rollback of a Renovate bump,
  the job opens a PR (with the Renovate app token, so CI runs on it) that
  reverts the bump and adds an exact-version exclusion for that package to the
  ceilings section of `renovate.json5`, naming the issue. It automerges like any
  green Renovate PR. Without this, the next compose change redeploys the bad
  version.
- **Informed / proven:** as 4.a; the rehearsal also covers a broken compose
  change to the canary service.

### 4.c Data stores

Scope: main Postgres (`pgvector/pgvector`), the other Postgres instances
(Twenty, Firecrawl, Listmonk, Umami, Fider), Mongo, the Redis instances,
Qdrant, Meilisearch, FalkorDB, Garage, RabbitMQ. `deploy/volume-mounts.yaml`
is the inventory and already names a backup kind per data volume (`sql`,
`rdb`, `tar`, `snapshot`, `archive`); snapshot-before reuses those methods.

- **Snapshot before:** immediately before an image change to a data store, or
  to an app that migrates its own database (4.b), the deploy takes a snapshot
  on core-01: per-database `pg_dump` for app databases, a cold copy of the
  volume for engines that cannot downgrade (Qdrant, Meilisearch, Mongo), RDB for
  Redis and FalkorDB. It first checks free disk against twice the volume size
  and refuses the upgrade (red, issue) when the room is not there; the disk
  reached 100% once already, on 2026-09-18.
- **Broken means:** container health, then a store-specific probe that compares
  before and after (Postgres: connect plus row counts of a few named tables;
  Mongo: ping and FCV unchanged; Qdrant: `/readyz` and collection count;
  Meilisearch: `/health`, index and document counts; Redis/FalkorDB: `PING`,
  key count, graph list; Garage: status and bucket list), then the health of
  the services that depend on it, then E2E.
- **Rolled back:** stop the store, put the snapshot back, start the previous
  image, run the probe again. Only inside the 30-minute window of R4.
- **Cannot be rolled back:** writes after the snapshot; a Mongo FCV bump, which
  therefore stays a separate human step; a Postgres major, which stays a
  scripted dump-and-restore on a copy.
- **Automated after this spec:** patches and minors of every store listed
  above, one minor at a time (the Twenty pattern). For Postgres a Renovate
  "minor" (18.6 to 18.7) is a Postgres minor release with the same on-disk
  format, which makes it the safest case, yet today the "Data store series
  bumps" rule holds it for review. Majors: see D1 and D2.
- **Proven:** a restore rehearsal on the 1st and 15th (02:17 UTC) restores
  each store's latest snapshot into a throwaway container on core-01 (never
  the live volume) and requires the restored facts to equal the snapshot's
  baseline. Twice a month rather than monthly because VictoriaLogs keeps 30
  days: a monthly run would leave the missing-rehearsal rule without a line
  on the 31st of a long month. As built (S8b): Garage is copied cold as well,
  because `garage meta snapshot` holds only `db.lmdb` and a restore also needs
  the node key and layout; a Mongo whose FCV changed since its snapshot is
  refused (`mongo-fcv`), and dry mode is refused for the cold-copied engines,
  whose previous image cannot open a volume the new one already opened.

### 4.d public-01

public-01 (website, Uptime Kuma, Caddy, Alloy) deploys itself from the private
infra repo every 10 minutes. Its script validates compose, Caddy and Alloy
config, backs Kuma up before a compose change and checks health afterwards.
Before S10 a failed check left the new files in place, and because the applied
revision was only recorded on success, the same broken revision was retried
on every run.

- **Broken means:** the existing post-apply checks (Kuma healthy, Alloy running,
  the site and status page answering).
- **Rolled back:** the previous files (copied aside before install), `compose up
  -d`, the same checks. The failed revision is recorded so the cron does not
  reapply it every 10 minutes; a newer revision on `main` is tried normally.
- **Cannot be rolled back:** Kuma's own database after a Kuma image migration;
  the existing pre-change Kuma backup is the restore source, used by hand.
- **Informed:** one JSON line per run in `/opt/klai/logs/infra-deploy.log`,
  shipped by public-01's Alloy to VictoriaLogs; Grafana on core-01 mails on an
  `auto_rollback` event and on two hours without a clean run (section 8).
  Nothing goes to Uptime Kuma: Kuma is the customer status page and is not
  used for operational signals (owner decision, 2026-10-28).
- **Proven:** a shell test with stubbed docker in that repo applies a bad
  revision and asserts the previous files are back.

## 5. Renovate policy that follows (layer e)

Once 4.a to 4.c are live and their rehearsals are green:

- Extend `helpers:pinGitHubActionDigests`. Today 28 `uses:` references are
  SHA-pinned and 192 are tag-pinned.
- `minimumReleaseAge: '3 days'` (was 7; Dependabot's default, and the
  hijacked npm releases of 2025 were gone after 2.5 to 12 hours). A fix for a
  published vulnerability skips it, and its lock-file update runs with a
  Renovate-side npmrc that lifts npm's own cooldown for that PR; uv's
  cooldown is lifted per package with `exclude-newer-package` when needed.
- Everything automerges: patches, minors and majors, data stores included
  (owner decision 2026-10-29, AGENTS.md "automatic by default, proof instead
  of approval"). The proof after the deploy is the gate. Decisions D1 and D2
  below are withdrawn; the Postgres-major migration and the Mongo FCV bump
  are built into the data-store helper so those majors succeed instead of
  rolling back.
- The only holds left are ceilings that pin a reproduced breakage, each with
  what lifts it (typescript-eslint, VictoriaLogs healthcheck, react-table
  migration, spacy wheels, eslint-config-next, ragas).
- Vexa automerges since the compose sync pre-pulls the bot image (`deploy/scripts/vexa-bot-prepull.sh`) before it replaces any file.

## 6. E2E-triggered rollback

`E2E prod-tenant` runs after each deploy in one serial queue and also on a
04:00 UTC schedule. The deploy-key-holding work stays out of the E2E job: a new
workflow `Auto-rollback` runs on `workflow_run` of `E2E prod-tenant`.

- On success it marks the current ledger position as last-green on core-01.
- On failure of a run that was triggered by a deploy (not by the schedule), it
  rolls back every ledger entry newer than last-green, in reverse order, then
  dispatches E2E again. Data entries follow R4.
- A scheduled red with nothing in the ledger since last-green rolls back
  nothing; that is drift outside our deploys (OS update, provider, expired
  credential) and becomes an issue only.
- E2E retries a failing spec once before it counts as red, so one flaky spec
  does not revert a good deploy.

## 7. Error-rate watch (slice S11)

Caddy 5xx is already alerted on (`caddy_5xx_count_high`). The post-deploy watch
(`deploy/scripts/post-deploy-error-watch.sh`, gate 4 of
`deploy-with-rollback.sh --error-watch <seconds>`) counts a service's
`level:error` lines in VictoriaLogs from the moment the recreate starts, over a
300-second window, and rolls the deploy back when BOTH hold: more than 10 error
lines since the deploy, and more than twice the lines in the same length of
time just before it. The second clause keeps trouble that began before the
deploy (an upstream already down, a database still loading) from being blamed
on it. Measured on 2026-10-28 over 246 deploys in 30 days: the floor alone would
have fired 3 times, all on errors already running before the deploy; with the
before-clause 0 of 246. A query error is fail-open (the deploy stays, with a
warning): an observability outage is no evidence against the new image. Caddy is
not watched this way (two deploys in 30 days, and its own 5xx rule covers it).

## 8. Dead-man's switch (layer f)

The chain fails silently more often than it fails loudly, so each link proves it
ran. Uptime Kuma is the customer status page and is not used for this (owner
decision, 2026-10-28): every signal below is a Grafana alert rule on core-01
that mails the ops contact point.

- **Weekly rehearsal** (`rollback-rehearsal.yml`): a `rollback-canary` service
  in the compose file (tiny HTTP image, no customer traffic, no data) is
  deployed with a known-bad image through the real rollback script. The job
  asserts the canary runs the previous image again within 5 minutes and that a
  report with outcome `rolled_back` was produced, then appends one
  `rollback_rehearsal` event line to the auto-rollback log that Alloy ships.
  A red job opens the `ci-failure` issue.
- Grafana rules: no `rollback_rehearsal` event with outcome `ok` in 8 days;
  any `auto_rollback` event (outcome `rolled_back` or `rollback_failed`) in the
  last 15 minutes; no successful Renovate run in 6 hours and no deploy-compose
  run in 7 days, both read from the ledger and report files that the sync
  writes on every run (a scheduled check on core-01 turns their age into a
  metric). Known ceiling: Grafana on core-01 watches core-01, so a dead
  Grafana or a dead host is caught only by the existing
  `public01_caddy_logs_silent` style rules in the other direction and by the
  alerter heartbeat already in place.
- `version-drift.yml` keeps checking for stuck Renovate PRs and runs.

## 9. Hook for a later AI fixer (layer g)

Out of scope to build; in scope to leave the hook:

- The rollback script prints one machine-readable report line (service, kind,
  from, to, commit, failed gate, outcome, log excerpt location). The deploy
  workflow uploads it as artifact `rollback-report`.
- `ci-failure-notice.yml` reads that artifact and labels the issue
  `auto-rollback` or `rollback-failed`, report in the body. Learned on
  2026-10-28: GitHub matches a `workflow_run` `workflows:` filter with its
  pattern syntax, so a workflow name with ` + ` in it never matched itself and
  no consumer of the compose sync had fired since August; workflow names stay
  free of `*`, `?`, `+`, `[` and `!`.
- A future fixer triggers on `workflow_run` of `CI failure notice` (or the
  label) and must, per field practice: hold no deploy keys or production
  secrets; be unable to edit `.github/workflows/**`, tests or this spec; stop
  after 2 attempts per rollback; and treat the report, logs and PR text as
  untrusted input (PromptPwnd; "Comment and Control", April 2026). The fixer's
  own runs fall under the dead-man's switch.

## 10. Acceptance tests

Phrased as outcomes someone can watch happen.

- AT-a1: A retrieval-api image that exits on start, deployed through the
  workflow, leaves core-01 running the previous image within 5 minutes; the run
  is red with outcome `rolled_back`; an issue labelled `auto-rollback` exists;
  a Grafana mail arrives.
- AT-a2: An image that starts but whose health endpoint answers 500 is rolled
  back the same way.
- AT-a3: A portal-api deploy whose migration adds a column and whose image is
  then rolled back leaves the previous image serving with the column present.
- AT-a4: A PR that adds `op.drop_column` together with application code fails
  `quality`; the same migration alone passes.
- AT-a5: When the previous image also fails, the run reports
  `rollback_failed`, stops after one attempt and the issue is labelled
  `rollback-failed`.
- AT-b1: A compose change that gives the canary service an invalid command is
  rolled back: the `*.prev` files are back on core-01 and the canary runs.
- AT-b2: After a rollback of a Renovate image bump, a PR exists that reverts
  the bump and excludes that exact version; once merged, Renovate does not
  propose that version again.
- AT-b3: A red gate for an image that migrates its own database (before S8)
  opens `rollback-failed` and leaves the new image in place.
- AT-c1: A Redis or Postgres minor bump takes a snapshot first; with a probe
  forced red, the store returns to the previous image and data within the
  window, and the row/key counts match the snapshot.
- AT-c2: With less free disk than twice the volume size, the upgrade does not
  start and an issue explains why.
- AT-c3: A gate going red 31 minutes after a data store upgrade opens an issue
  and restores nothing.
- AT-c4: The twice-monthly restore rehearsal restores every store in the
  inventory into a throwaway container and the restored facts equal the
  snapshot baseline; a store whose restore fails opens an issue.
- AT-d1: A klai-infra revision with a Caddyfile that validates but fails the
  live check leaves public-01 on the previous files, logs an `auto_rollback`
  line that Grafana mails on, and the next cron run does not reapply that
  revision.
- AT-e1: `renovate.json5` passes `test-renovate-config.sh` with SHA pinning and
  the new automerge rules; every `uses:` reference is a 40-character SHA after
  the first Renovate run.
- AT-f1: Disabling the Renovate workflow for 6 hours produces a Grafana mail;
  skipping one weekly rehearsal produces a Grafana mail after 8 days.
- AT-f2: The weekly rehearsal is green on `main` for four weeks in a row.
- AT-6: A deploy followed by a red E2E run (red after one retry) rolls back
  that deploy's ledger entries and re-runs E2E green; a red scheduled 04:00 run
  with an empty ledger rolls back nothing.

## 11. Rollout slices

One PR each, one to three hours each. Size is the expected class (S up to 50
changed lines, M up to 200, L above). Gates for every slice that touches deploy
scripts or workflows: `bash -n` on changed scripts, `python3
scripts/check-workflow-shell-syntax.py`, the slice's `deploy/scripts/tests/*.test.sh`
added to `deploy-scripts-tests.yml`, and the rehearsal once it exists.

| # | Slice | Section | Files it may touch | Acceptance | Size | Executor |
|---|-------|---------|--------------------|------------|------|----------|
| S1 | `deploy-with-rollback.sh`: record previous, pull by `<sha>`, `compose-up.sh`, HTTP probe, retag on red, ledger, report line, exit 0/3/4; switch the per-service workflows to it | 3, 4.a | `deploy/scripts/deploy-with-rollback.sh`, its test, the service workflows listed in 4.a except portal-api, `deploy-scripts-tests.yml`, `deploy-compose.yml` (install path) | AT-a1, a2, a5 | M | Sonnet |
| S2 | Same for portal-api inside `deploy-portal-api.sh`; expand/contract CI check for portal-api and knowledge-ingest Alembic | 3 R3, 4.a | `deploy-portal-api.sh`, its test, `portal-api.yml`, `knowledge-ingest.yml`, a check script under `scripts/` | AT-a3, a4 | M | Opus |
| S3 | `deploy-compose.yml`: keep `*.prev`, roll back compose and config on a red gate, ledger, report; images that migrate their own DB report `rollback_failed` | 4.b | `deploy-compose.yml`, a helper under `deploy/scripts/`, its test | AT-b1, b3 | M | Sonnet |
| S4 | Notification and hook: report artifact, labels in `ci-failure-notice.yml`, Grafana rule on the `auto_rollback` log line | 4.a, 9 | `ci-failure-notice.yml`, the deploy workflows (upload step), `deploy/grafana/provisioning/alerting/` | AT-a1 issue and mail part | M | Sonnet |
| S5 | Revert PR plus exact-version exclusion after a Renovate bump rollback | 4.b | `deploy-compose.yml`, a script under `scripts/`, `renovate.json5` (ceiling section only) | AT-b2 | M | Opus (app token) |
| S6 | `Auto-rollback` workflow on E2E result, last-green marker, ledger replay, one spec retry in the prod suite | 6 | new workflow, helper under `deploy/scripts/`, `klai-portal/frontend` Playwright prod config | AT-6 | M | Opus (deploy key) |
| S7 | Rehearsal and dead-man's switch: `rollback-canary` service, `rollback-rehearsal.yml`, Grafana mail rules (no Kuma) | 8 | `deploy/docker-compose.yml`, `deploy/volume-mounts.yaml` if needed, new workflow, `renovate.yml`, alerting rules | AT-f1, f2 | M | Sonnet |
| S8a | Snapshot, probe, restore for Postgres (main and app databases) and the apps that migrate their own DB | 4.c, 4.b | helper under `deploy/scripts/`, its test, `deploy-compose.yml` | AT-c1, c2, c3 for Postgres | L | Opus |
| S8b | Same for Qdrant, Meilisearch, Mongo, Redis, FalkorDB, Garage, RabbitMQ; restore rehearsal on the 1st and 15th | 4.c | same helper, new rehearsal workflow | AT-c1 to c4 | L | Opus |
| S9 | Renovate policy: SHA pinning, vulnerability bypass, everything automerges (owner decision 2026-10-29) | 5 | `renovate.json5`, `scripts/test-renovate-config.sh` | AT-e1 | S | Sonnet |
| S10 | public-01 previous-revision rollback and bad-revision marker (private infra repo) | 4.d | that repo's deploy script and its test | AT-d1 | S | Sonnet |
| S11 | Post-deploy error-rate watch | 7 | helper under `deploy/scripts/`, alerting rules | threshold from 30 days of data, one forced breach rolls back | M | Sonnet |

Order: S1, S3, S4, S7 first (stateless rollback, notified and rehearsed, for
both our images and third-party images), then S5 and S6, then S2, then S8a/S8b,
then S9 (which depends on S8 for data-store minors and on D1/D2 for majors),
S10 and S11. S9's SHA-pinning part has no dependency and can ship any time.

## 12. Open decisions for the owner

- **D1.** Withdrawn 2026-10-29 by the owner: Postgres majors automerge; the
  helper migrates the data directory (dump, initdb on the new major, restore)
  inside the deploy and puts the old directory back on a red gate.
- **D2.** Withdrawn 2026-10-29 by the owner: majors of every data store
  automerge, with the snapshot-and-restore path as the gate and the Mongo FCV
  bump automated after the rollback window.
- **D3.** Automatically merge a revert of our own code after an image rollback?
  Recommendation: no. Roll back the image, label the issue, and let the next
  commit (or the later AI fixer) carry the fix; an unrelated commit that
  redeploys the bug is rolled back again, which is noisy but safe.
- **D4.** Move core-01 and public-01 to Flatcar or Fedora CoreOS for atomic OS
  rollback later? Recommendation: not now. Add a reboot-required and
  failed-unattended-upgrade alert first and revisit when a host is rebuilt
  anyway.
- **D5.** Is a 30-minute window right for automatic data restores (R4)?
  Recommendation: yes; it covers the deploy, its E2E run and one retry, and
  bounds the writes a restore can lose.
