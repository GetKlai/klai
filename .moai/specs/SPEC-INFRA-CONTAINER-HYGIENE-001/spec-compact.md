# SPEC-INFRA-CONTAINER-HYGIENE-001 — Compact (v0.3.0)

Auto-extract van requirements + acceptance + files + exclusions voor
`/moai run`.

## Wat is veranderd in v0.3.0

- **Tenant-provisioning architectuur correct opgenomen.** Klai heeft
  TWEE legitieme klassen prod-containers (compose-managed +
  provisioning-managed), niet één.
- **REQ-2 herschreven** naar code-fix: tenant-LibreChats SHALL
  `klai.managed_by=portal-api-provisioning` + `klai.tenant_slug=<slug>`
  + `klai.kind=librechat` labels dragen, gezet door
  `_start_librechat_container`.
- **REQ-2c uit v0.2.0 (compose-block voor librechat-voys) vervalt.**
  Vervangen door REQ-2a (label-fix) + REQ-2b (eenmalige backfill).
- **REQ-1, REQ-5, REQ-2c, REQ-2d** checken UNION van beide
  label-klasses, niet alleen compose-project label.

## Requirements (EARS)

### R1 — PreToolUse hook blokkeert destructieve docker-acties
WHEN een Bash tool-call matcht `docker rm`/`rmi`/`volume rm`/
`system prune`/`compose down --volumes`, THEN
`.claude/hooks/klai/container-hygiene-preflight.sh` SHALL eerst draaien.
Hard-blocks: `volume prune`, `image prune -af`, `system prune -a`,
`compose down --volumes`. Tenant-pattern + compose-history blocks
voor targeted operations. Block-bericht voor tenant-pattern verwijst
naar portal-api deprovision-flow.

### R2 — Twee legitieme klassen, beide met label

**Klasse A — Compose-managed:** `com.docker.compose.project=klai-core`
(automatisch). Geen extra werk.

**Klasse B — Provisioning-managed:** SHALL `klai.managed_by=portal-api-provisioning`
+ `klai.tenant_slug=<slug>` + `klai.kind=<type>` labels dragen.

**REQ-2a:** code-fix in `_start_librechat_container` — labels-kwarg
in `client.containers.run()`.
**REQ-2b:** eenmalige backfill van librechat-voys (recreate met labels).
**REQ-2c:** CI-guard `audit-compose-orphans.sh` matcht Caddy-upstreams
tegen UNION van compose-services + provisioning-name-patterns.
**REQ-2d:** post-deploy snapshot na `compose up -d` checkt UNION
van beide label-klasses; flagt containers zonder enige.

### R3 — Deploy-wrapper met `--remove-orphans` ingebouwd
Eén script `klai-infra/deploy/scripts/compose-up.sh`. Alle 10
service-deploy-workflows roepen via SSH dit script aan. Veilig naast
klasse-B containers omdat `--remove-orphans` alleen klasse-A targets.

### R4 — VERVALLEN
Image-pinning. VERSIONS.md is canoniek.

### R5 — Audit als VictoriaLogs event-stream
`scripts/docker-orphan-audit.sh` weekly zondag 03:00 op core-01,
emit structlog-events naar VictoriaLogs. Detecteert containers die
GEEN klasse-A EN GEEN klasse-B label dragen (UNION-check), naast de
overige categorieën uit v0.2.0. **REPORT-ONLY.**

### R6 — Daily safe cleanup via systemd timer
`docker-cleanup.timer` daily 03:00:
`docker image prune -f`, `container prune -f --filter until=24h`,
`network prune -f`, `builder prune -f --filter until=72h`.
NOOIT `volume prune` of `image prune -af`.

### R7 — Pitfall-documentatie
`container-cleanup-without-preflight (HIGH)` met incident +
mechanical-vs-narrative + tenant-provisioning klasse-context.

## Files Affected

### klai (deze repo)
- NIEUW: `.claude/hooks/klai/container-hygiene-preflight.sh`
- UITGEBREID: `.claude/settings.json` (PreToolUse-hook registratie)
- NIEUW: `.claude/rules/klai/infra/container-hygiene.md`
- UITGEBREID: `.claude/rules/klai/pitfalls/process-rules.md`
- UITGEBREID: `klai-portal/backend/app/services/provisioning/infrastructure.py`
  (REQ-2a labels)
- NIEUW: `klai-portal/backend/tests/services/provisioning/test_infrastructure_labels.py`
- NIEUW: `.moai/specs/SPEC-INFRA-CONTAINER-HYGIENE-001/{spec,plan,acceptance,research,spec-compact,progress}.md`

### klai-infra (cross-repo)
- NIEUW: `deploy/scripts/compose-up.sh` (REQ-3)
- NIEUW: `scripts/audit-compose-orphans.sh` + `test-audit-compose-orphans.sh` (REQ-2c)
- NIEUW: `scripts/audit-orphan-snapshot.sh` (REQ-2d)
- NIEUW: `scripts/docker-orphan-audit.sh` (REQ-5)
- UITGEBREID: `.github/workflows/audit-compose.yml`
- UITGEBREID: 10× `.github/workflows/*.yml`
- NIEUW: `core-01/systemd/docker-cleanup.{service,timer}` (REQ-6)
- NIEUW: `core-01/systemd/orphan-audit.{service,timer}` (REQ-5)

### Operator-acties op core-01
- `systemctl link` + `enable --now` voor beide timers
- Eenmalig: librechat-voys recreate met klasse-B labels (REQ-2b backfill)

## Acceptance Criteria

| AC | Wat | Mechanische verificatie |
|---|---|---|
| AC-1 | Hook blokkeert `docker rm <tenant-container>` | exit-code 2 + JSON "BLOCKED" + provisioning-flow verwijzing |
| AC-2 | Tenant-LibreChats dragen drie klasse-B labels | `docker inspect` toont alle drie; unit-test op `_start_librechat_container` |
| AC-3 | Deploy-wrapper veilig naast klasse-B containers | scripted wees-test, librechat-voys overleeft |
| AC-4 | Daily timer >=2 successful runs, dangling <50, named volumes intact | `journalctl` + `docker images` + volume-diff |
| AC-5 | Audit-stream UNION-check werkt | LogsQL query, zero `orphan_no_managed_label` voor prod containers |
| AC-6 | Hook gebruikt audit-stream + onderscheidt klasse-B | end-to-end test op dev-stack |
| AC-7 | Pitfall + CI-guard | grep + CI-fixture |

## Run Acceptance Aggregate

- AC-1 t/m AC-7 allemaal binnen 7 dagen na laatste deploy
- Geen productie-incident causaal aan deze SPEC's deploys
- 30d post REQ-6: dangling-count consistent <50
- 30d post REQ-1: minstens 1 `event:hook_blocked` in VictoriaLogs
- 30d post REQ-2 + REQ-5: zero `event:orphan_no_managed_label`
  buiten gelabelde `klai.adhoc=*`
- librechat-voys draagt klasse-B labels (post-backfill)
- Toekomstige tenant-provisioning landt automatisch met labels

## Exclusions

- Image-pinning policy/pilot (VERSIONS.md is canoniek)
- Server-side enforcement van handmatige SSH `docker rm`
- CI-rule die alle docker-run patterns weert
- Watchtower / auto-pull tools
- Vexa recordings volume
- GHCR retention policy
- Kubernetes / multi-server hygiene
- `librechat-getklai` klasse-B labels (blijft compose-managed klasse A)
- Generaliseren label-schema naar future tenant-services (eigen SPEC)

## Implementation Volgorde (STRIKT)

1. **klai-PR Stage 1 (gedaan, 55964c56):** REQ-1 hook + REQ-7 pitfall
   + REQ-1 narrative.
2. **klai-PR Stage 1.5 (deze v0.3.0 update):** REQ-2a code-fix in
   `_start_librechat_container` + tests, hook block-bericht update,
   narrative + pitfall update voor klasse-B nuance, REQ-2b
   handmatige backfill librechat-voys op core-01.
3. **klai-infra-PR Stage 2:** REQ-3 compose-up.sh + docs.yml pilot
   (24u monitoring).
4. **klai-infra-PR Stage 3:** REQ-3 rollout naar 9 overige workflows.
5. **klai-infra-PR Stage 4:** REQ-2c + REQ-2d audit-compose-orphans
   + post-deploy snapshot.
6. **klai-infra-PR Stage 5:** REQ-6 systemd cleanup timer + activatie.
7. **klai-infra-PR Stage 6:** REQ-5 audit-stream + Grafana panel.

Worktree-pattern: één per repo. Geen mega-PR.

## Open Questions voor `/moai run`

1. Hook fail-mode bij ontbrekende deps: fail-closed jq/curl, fail-open
   SSH reachability.
2. Per-workflow `--remove-orphans` of compose-up.sh wrapper? Wrapper
   voorkeur.
3. Alloy stdout-pickup bevestiging voor REQ-5 emit-via-stdout pattern.
4. `librechat-getklai` óók klasse-B labels? Voorkeur: nee, blijf
   compose-managed.
