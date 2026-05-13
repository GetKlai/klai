# SPEC-INFRA-CONTAINER-HYGIENE-001 — Implementation Plan (v0.3.0)

## Approach

Drie lagen, zes requirements (REQ-4 vervallen). v0.3.0 corrigeert de
fundamentele aanname uit v0.2.0 dat alle prod-containers via compose
managed worden. Klai heeft TWEE legitieme klassen prod-containers:

- **Compose-managed** (klasse A): label `com.docker.compose.project=klai-core`
- **Provisioning-managed** (klasse B): label
  `klai.managed_by=portal-api-provisioning` plus `klai.tenant_slug=<slug>`
  en `klai.kind=<type>` — gezet door `_start_librechat_container` in
  portal-api

REQ-1 hook, REQ-2 code-fix, REQ-2c CI-guard, en REQ-5 audit checken
op de UNION van beide klasses. Een container zonder enige label uit
beide klasses (en zonder `klai.adhoc=*` opt-in) is een wees.

REQ-2c uit v0.2.0 (librechat-voys hard-coderen in compose) is vervangen
door REQ-2a (label-fix in `_start_librechat_container`) plus
REQ-2b (eenmalige backfill voor bestaande tenant-containers).

Het werk verschuift van "klai-infra compose-edit" naar "klai code-edit
in portal-api" — REQ-2a landt in dezelfde klai-repo PR als REQ-1+REQ-7.
Dat versnelt de uitrol omdat de tenant-provisioning fix dichter bij de
trigger ligt.

## Task Decomposition

| # | Task | Files | Repo | Risk | Stage |
|---|---|---|---|---|---|
| 1 | preflight-hook script (basis) | `.claude/hooks/klai/container-hygiene-preflight.sh` | klai | Laag | 1 (done) |
| 2 | hook in settings.json | `.claude/settings.json` | klai | Laag | 1 (done) |
| 3 | narrative rule + pitfall (basis) | `.claude/rules/klai/infra/container-hygiene.md`, `.claude/rules/klai/pitfalls/process-rules.md` | klai | Laag | 1 (done) |
| 4 | REQ-2a: labels in `_start_librechat_container` + tests | `klai-portal/backend/app/services/provisioning/infrastructure.py`, `klai-portal/backend/tests/services/provisioning/test_infrastructure_labels.py` | klai | Medium | 1.5 |
| 5 | Hook update: block-bericht verwijst naar provisioning-flow | hook script | klai | Laag | 1.5 |
| 6 | Narrative + pitfall: twee-klassen sectie | rule + pitfall | klai | Laag | 1.5 |
| 7 | REQ-2b: handmatige re-run librechat-voys met labels | core-01 ssh handmatig | runtime | Medium (~30s downtime) | 1.5 |
| 8 | REQ-3: `compose-up.sh` deploy-wrapper | `deploy/scripts/compose-up.sh` | klai-infra | Medium | 2 |
| 9 | REQ-3 pilot: `docs.yml` workflow | `.github/workflows/docs.yml` | klai-infra | Laag | 2 |
| 10 | REQ-3 rollout: 9 overige workflows | `.github/workflows/*.yml` | klai-infra | **HIGH** | 3 |
| 11 | REQ-2c: `audit-compose-orphans.sh` + regression | `scripts/audit-compose-orphans.sh`, `scripts/test-audit-compose-orphans.sh` | klai-infra | Laag | 4 |
| 12 | `audit-compose.yml` workflow uitbreiden | `.github/workflows/audit-compose.yml` | klai-infra | Laag | 4 |
| 13 | REQ-2d: `audit-orphan-snapshot.sh` post-deploy | `scripts/audit-orphan-snapshot.sh` | klai-infra | Laag | 4 |
| 14 | REQ-6: systemd `docker-cleanup.{service,timer}` | `core-01/systemd/docker-cleanup.{service,timer}` | klai-infra | Laag | 5 |
| 15 | Activeer cleanup-timer op core-01 | `systemctl link`, `enable --now` | runtime | Laag | 5 |
| 16 | REQ-5: `docker-orphan-audit.sh` event-emitter | `scripts/docker-orphan-audit.sh` | klai-infra | Laag | 6 |
| 17 | systemd `orphan-audit.{service,timer}` | `core-01/systemd/orphan-audit.{service,timer}` | klai-infra | Laag | 6 |
| 18 | Activeer audit-timer op core-01 | `systemctl link`, `enable --now` | runtime | Laag | 6 |
| 19 | Grafana panel + alert | dashboard JSON | klai-infra | Laag | 6 |

Tasks 1-3 reeds compleet (Stage 1, commit `55964c56`). Tasks 4-7 in
Stage 1.5 (deze klai-PR uitbreiding). Tasks 8+ in klai-infra stages.

## Files Affected (delta v0.3.0)

### klai (deze repo) — toegevoegd in v0.3.0

- **Uitgebreid:** `klai-portal/backend/app/services/provisioning/infrastructure.py`
  — `labels={...}` in `client.containers.run()` van
  `_start_librechat_container` (REQ-2a, ~5 regels code).
- **Nieuw:** `klai-portal/backend/tests/services/provisioning/test_infrastructure_labels.py`
  — verifieert labels-aanwezigheid via mock van docker-client.
- **Uitgebreid:** `.claude/hooks/klai/container-hygiene-preflight.sh`
  — block-bericht voor tenant-pattern verwijst naar
  provisioning-deprovision-flow.
- **Uitgebreid:** `.claude/rules/klai/infra/container-hygiene.md`
  — sectie "Twee legitieme klassen" + label-conventie.
- **Uitgebreid:** `.claude/rules/klai/pitfalls/process-rules.md`
  — pitfall-tekst genuanceerd voor tenant-provisioning klasse.

### klai-infra (cross-repo) — minor delta

REQ-2c CI-guard accepteert nu naast compose-service-namen óók
`librechat-*` als geldige naam-pattern. REQ-2d en REQ-5 scripts
checken UNION van beide labels.

## Technology Choices (delta v0.3.0)

- **Labels via `client.containers.run(labels=...)`** — Docker labels
  zijn immutable na container creation. Voor backfill: container
  recreate met nieuwe args.
- **Drie aparte labels** ipv één geconcateneerde — Docker label-filter
  ondersteunt key-based filtering (`docker ps --filter label=klai.managed_by=...`).
- **`klai.*` namespace** — convention-aligned met `klai.adhoc` uit
  REQ-7. Reserveert namespace voor toekomstige uitbreidingen.

## Risks & Mitigations (delta v0.3.0)

| Risk | Mitigation |
|---|---|
| `_start_librechat_container` patch breekt tenant-provisioning | Tests in `test_infrastructure_labels.py` mocken docker-client en verifiëren labels-aanwezigheid. |
| Backfill librechat-voys faalt of veroorzaakt downtime | ~30s downtime, zelfde als recovery van vandaag. Backup `librechat.yaml` + env-file zijn al op disk. |
| `librechat-getklai` (compose-managed) — moet die ook klasse-B labels? | NEE. Klasse A is sufficient. Audit-tools nemen UNION van beide klasses. |
| Hook block-bericht verwijst naar deprovision-flow die niet bestaat | Portal-api heeft `deprovision_tenant` in `orchestrator.py` — block-bericht verwijst daarheen. |

Overige risico's identiek aan v0.2.0.

## Open Vragen voor `/moai run` (delta v0.3.0)

1. **`librechat-getklai` óók klasse-B labels?** Voorkeur: nee, blijf
   compose-managed. Beslissing tijdens REQ-2 review.
2. **Toekomstige `klai.kind` waardes?** Voor nu alleen `librechat`.
3. **Override-pad bij tenant-deprovisioning:** Operator via SSH
   buiten Claude-sessie. Hook ziet dat niet. Gedocumenteerd in pitfall.

## Success Criteria (delta v0.3.0)

- librechat-voys draagt na backfill klasse-B labels (verifieerbaar via
  `docker inspect`).
- Test-fixture `test_infrastructure_labels.py` slaagt.
- 7 dagen na deploy: zero `event:orphan_no_managed_label` voor running
  containers (post-backfill).

## Out of Scope

- Labels-backfill voor andere containers dan librechat-voys.
- Generaliseren van label-schema naar toekomstige tenant-services
  (volgt patroon, eigen SPEC wanneer relevant).
- Volume-labels (apart probleem, klantdata-risico).

## Ordering & Branch Strategy

**Stage 1 (klai PR, gedaan, commit `55964c56`):** REQ-1 hook + REQ-7
pitfall + narrative rule.

**Stage 1.5 (klai PR, v0.3.0 uitbreiding):**
- REQ-2a: `_start_librechat_container` labels + tests
- REQ-2b: backfill librechat-voys op core-01
- Hook + narrative + pitfall update voor klasse-B nuance
- SPEC bump v0.2.0 → v0.3.0

**Stage 2 (klai-infra PR):** REQ-3 deploy-wrapper + docs.yml pilot.

**Stage 3 (klai-infra PR):** REQ-3 rollout naar 9 overige workflows.

**Stage 4 (klai-infra PR):** REQ-2c + REQ-2d (audit-compose-orphans
+ post-deploy snapshot).

**Stage 5 (klai-infra PR):** REQ-6 systemd cleanup timer.

**Stage 6 (klai-infra PR):** REQ-5 audit-stream + Grafana.

Geen mega-PR. Stages 2-6 elk in eigen PR voor regressie-isolatie.

## Annotation Hooks

- `// NOTE:` op de drie label-keuzes (`klai.managed_by`, `tenant_slug`, `kind`)
- `// NOTE:` op fail-mode keuze hook (fail-closed deps, fail-open SSH)
- `// NOTE:` op compose-up.sh flag-set
