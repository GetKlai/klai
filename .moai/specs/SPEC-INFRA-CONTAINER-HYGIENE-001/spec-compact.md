# SPEC-INFRA-CONTAINER-HYGIENE-001 — Compact (v0.2.0)

Auto-extract van requirements + acceptance + files + exclusions voor
`/moai run`.

## Wat is veranderd in v0.2.0

- **REQ-4 geschrapt.** `:latest` op `ghcr.io/getklai/*` is bewust
  beleid per `deploy/VERSIONS.md` + `docs/runbooks/version-management.md`.
- **REQ-1, REQ-2, REQ-3, REQ-5 herschreven naar AI-first mechanisch.**
  Geen markdown-rules; in plaats daarvan: PreToolUse hook, CI-guard,
  deploy-wrapper, VictoriaLogs event-stream.

## Requirements (EARS)

### R1 — PreToolUse hook blokkeert destructieve docker-acties
WHEN een Bash tool-call matcht `docker rm`/`rmi`/`volume rm`/
`system prune`/`compose down --volumes`, THEN
`.claude/hooks/klai/container-hygiene-preflight.sh` SHALL eerst draaien
en de checks uitvoeren: Caddy upstream, compose git-history,
tenant-naam, depends_on, recent-traffic via VictoriaLogs. Eén positieve
match = exit 1 = tool-call blocked. Hard-block voor `volume prune`
en `image prune -af`. Geregistreerd in `.claude/settings.json`.

### R2 — Compose als single source, mechanisch geverifieerd
**R2a (preventief):** `scripts/audit-compose-orphans.sh` draait in
bestaande `audit-compose.yml` workflow op elke PR; faalt bij
inconsistenties tussen Caddyfile, compose-services, en
container_names. **R2b (detectief):** `compose-up.sh` (REQ-3) draait
post-deploy `audit-orphan-snapshot.sh` die `event:orphan_post_deploy`
naar VictoriaLogs emit. **R2c (concreet):** `librechat-voys` SHALL
als service-block in `klai-infra/deploy/docker-compose.yml`.

### R3 — Deploy-wrapper met `--remove-orphans` ingebouwd
Eén script `klai-infra/deploy/scripts/compose-up.sh` SHALL het
canonieke deploy-mechanisme zijn — `docker compose pull && up -d
--remove-orphans` plus post-deploy snapshot. Alle 10 service-deploy-
workflows SHALL via SSH dit script aanroepen. **STRIKT NA REQ-2c**.

### R4 — VERVALLEN
Image-pinning pilot geschrapt. VERSIONS.md is canoniek.

### R5 — Audit als VictoriaLogs event-stream
`scripts/docker-orphan-audit.sh` SHALL elke zondag 03:00 op core-01
draaien en structlog-events emitten via stdout (Alloy → VictoriaLogs)
met `service:klai-orphan-audit` en zes mogelijke `event:` types
(orphan_no_compose_label, orphan_service_removed, image_untagged_old,
volume_unmounted, caddy_upstream_missing, tenant_container_no_route).
**REPORT-ONLY** — geen deletion. Grafana panel + alert. AI queryt via
VictoriaLogs MCP.

### R6 — Daily safe cleanup via systemd timer
`docker-cleanup.timer` SHALL daily 03:00 draaien:
`docker image prune -f`, `docker container prune -f --filter until=24h`,
`docker network prune -f`, `docker builder prune -f --filter until=72h`.
**NOOIT** `volume prune` of `image prune -af`.

### R7 — Pitfall-documentatie
`container-cleanup-without-preflight (HIGH)` pitfall in
`.claude/rules/klai/pitfalls/process-rules.md` met incident-tijdlijn
+ "why mechanical not narrative" sectie.

## Files Affected

### klai (deze repo)
- NIEUW: `.claude/hooks/klai/container-hygiene-preflight.sh`
- UITGEBREID: `.claude/settings.json` (PreToolUse-hook registratie)
- NIEUW: `.claude/rules/klai/infra/container-hygiene.md`
- UITGEBREID: `.claude/rules/klai/pitfalls/process-rules.md`
- NIEUW: `.moai/specs/SPEC-INFRA-CONTAINER-HYGIENE-001/{spec,plan,acceptance,research,spec-compact,progress}.md`

### klai-infra (cross-repo)
- NIEUW: `deploy/scripts/compose-up.sh` (REQ-3)
- NIEUW: `scripts/audit-compose-orphans.sh` + `test-audit-compose-orphans.sh` (REQ-2a)
- NIEUW: `scripts/audit-orphan-snapshot.sh` (REQ-2b)
- NIEUW: `scripts/docker-orphan-audit.sh` (REQ-5)
- UITGEBREID: `deploy/docker-compose.yml` (REQ-2c librechat-voys block)
- UITGEBREID: `.github/workflows/audit-compose.yml` (orphan-step)
- UITGEBREID: 10× `.github/workflows/*.yml` (`docker compose up` → `compose-up.sh` aanroep)
- NIEUW: `core-01/systemd/docker-cleanup.{service,timer}` (REQ-6)
- NIEUW: `core-01/systemd/orphan-audit.{service,timer}` (REQ-5)

### Operator-acties op core-01 (geen Git)
- `systemctl link` + `enable --now` voor beide timers
- Eenmalig: `librechat-voys` handmatig vervangen door compose-managed
  variant na REQ-2c merge

## Acceptance Criteria

| AC | Wat | Mechanische verificatie |
|---|---|---|
| AC-1 | Hook blokkeert `docker rm <tenant-container>` | exit-code 1 + "BLOCKED" in output; container nog aanwezig |
| AC-2 | librechat-voys is compose-managed | `docker inspect` returns `klai-core` project label |
| AC-3 | Deploy-wrapper + remove-orphans werkt; librechat-voys overleeft | scripted wees-test, librechat-voys still running |
| AC-4 | Daily timer >=2 successful runs, dangling <50, named volumes intact | `journalctl` + `docker images` + volume-diff |
| AC-5 | Audit-events queryable in VictoriaLogs | LogsQL query returns events; zero `orphan_no_compose_label` |
| AC-6 | Audit-events worden door REQ-1 hook gebruikt | end-to-end test: dummy orphan → audit detect → hook blokkeert |
| AC-7 | Pitfall + CI-guard | `grep` returns 0 in deze repo; klai-infra CI faalt op test-fixture |

## Run Acceptance Aggregate

- AC-1 t/m AC-7 allemaal binnen 7 dagen na laatste deploy
- Geen productie-incident causaal aan deze SPEC's deploys
- 30d post REQ-6: dangling-count consistent <50
- 30d post REQ-1: minstens 1 `event:hook_blocked` event in VictoriaLogs
- 30d post REQ-5: zero `event:orphan_no_compose_label` buiten gelabelde
  `klai.adhoc=*` containers

## Exclusions

- Image-pinning policy/pilot (VERSIONS.md is canoniek)
- Server-side enforcement van handmatige SSH `docker rm`
- CI-rule die alle docker-run patterns weert
- Watchtower / auto-pull tools
- Vexa recordings volume
- GHCR retention policy
- Kubernetes / multi-server hygiene

## Implementation Volgorde (STRIKT)

1. **klai-PR:** REQ-1 (hook script + settings.json) + REQ-7 (pitfall)
   + REQ-1 narrative — alle in deze repo, geen externe deps
2. **klai-infra-PR:** REQ-2c (librechat-voys compose-block) + handmatige
   container-vervanging op core-01
3. **klai-infra-PR:** REQ-3 (compose-up.sh) + 1 pilot-workflow (`docs.yml`),
   24u monitoring vóór bredere rollout
4. **klai-infra-PR:** REQ-3 rollout naar overige 9 workflows
5. **klai-infra-PR:** REQ-2a + REQ-2b (audit-compose-orphans +
   audit-orphan-snapshot)
6. **klai-infra-PR:** REQ-6 (systemd cleanup timer + activation)
7. **klai-infra-PR:** REQ-5 (audit-stream script + systemd timer +
   Grafana panel/alert)

Worktree-pattern: één per repo. Geen mega-PR.

## Open Questions voor `/moai run`

1. Hook fail-mode bij ontbrekende deps: fail-open of fail-closed?
   (voorkeur: fail-closed met `jq`/`curl` als hard requirement;
   `ssh core-01` reachability fail-open voor dev-machines)
2. Per-workflow `--remove-orphans` flag of compose-up.sh wrapper?
   (voorkeur: wrapper, maar uitwisselbaar met
   `COMPOSE_REMOVE_ORPHANS=true` env-var)
3. Bevestiging dat Alloy alle container-stdout zonder filter forward
   naar VictoriaLogs (voor REQ-5 emit-via-stdout pattern)
