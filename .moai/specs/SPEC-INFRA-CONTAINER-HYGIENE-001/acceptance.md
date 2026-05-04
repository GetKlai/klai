# SPEC-INFRA-CONTAINER-HYGIENE-001 — Acceptance Criteria (v0.3.0)

Zeven Given/When/Then scenarios. Elke AC is mechanisch verifieerbaar
— geen mens-leesbaar rapport om te beoordelen, geen "agent zou X
moeten doen". Alle checks via tool-output, exit-codes, query-resultaten.

v0.3.0 herzien: AC-2 verifieert nu provisioning-labels (klasse B) ipv
compose-block; AC-5/6 herzien voor UNION van twee label-klasses.

## AC-1 — Pre-tool hook blokkeert destructieve docker-acties

- **Given** REQ-1 is uitgevoerd (`.claude/hooks/klai/container-hygiene-preflight.sh`
  bestaat, `.claude/settings.json` heeft de PreToolUse-hook),
- **When** een Claude-sessie probeert een Bash tool-call met commando
  `docker rm librechat-voys` te draaien,
- **Then** de tool-call returneert exit-code 2 met JSON-decision
  payload bevattende het woord "BLOCKED" en een verwijzing naar de
  portal-api deprovision-flow, EN de container is NIET verwijderd
  (verifieerbaar via `docker ps --filter name=librechat-voys`).

**Test:** test-fixture met dummy `docker rm` poging, exit-code-assertie.
Plus drie negatieve cases (`docker logs`, `docker ps`, `docker exec`)
moeten exit-0 returnen — hook mag legitieme commando's niet blokkeren.

## AC-2 — Tenant-LibreChats dragen provisioning-labels (klasse B)

- **Given** REQ-2a is uitgevoerd (code-fix in
  `_start_librechat_container`) EN REQ-2b backfill is gedaan voor
  bestaande containers,
- **When** een Bash-command op core-01 draait
  `docker inspect librechat-voys --format '{{json .Config.Labels}}'`,
- **Then** de output bevat alle drie labels:
  `klai.managed_by=portal-api-provisioning`,
  `klai.tenant_slug=voys`, `klai.kind=librechat`. EN voor een
  toekomstige tenant-provisioning roundtrip (test-fixture in
  dev-stack): de nieuwe container heeft hetzelfde drie-tal labels
  na `provision_tenant()`.

**Test:**
- Unit-test `test_infrastructure_labels.py`: mock docker-client,
  verifieer `client.containers.run` werd aangeroepen met
  `labels={'klai.managed_by': 'portal-api-provisioning',
  'klai.tenant_slug': '<slug>', 'klai.kind': 'librechat'}`.
- Post-backfill verificatie via `docker inspect` op core-01 in
  PR-acceptance.

## AC-3 — Deploy-wrapper + `--remove-orphans` werkt mechanisch

- **Given** REQ-3 is uitgevoerd (alle 10 workflows roepen
  `compose-up.sh` aan) EN tenant-LibreChats dragen klasse-B labels
  (AC-2 hold),
- **When** een opzettelijke "wees-test" wordt uitgevoerd: een
  test-service `compose-orphan-test` wordt toegevoegd, gedeployd, dan
  uit compose verwijderd, en opnieuw gedeployd,
- **Then** na de tweede deploy is `compose-orphan-test` niet meer
  running, EN `librechat-voys` is NOG STEEDS running (omdat het
  klasse-B labels draagt — `docker compose --remove-orphans` raakt
  alleen klasse-A containers met dezelfde compose-project label).

**Test:** scripted regression `klai-infra/scripts/test-deploy-wrapper.sh`,
runs op staging.

## AC-4 — Daily safe cleanup timer draait succesvol

- **Given** REQ-6 is uitgevoerd,
- **When** 48 uur is verstreken sinds installatie,
- **Then** ten minste 2 successful runs in `journalctl -u
  docker-cleanup.service`, dangling-image count <50, named volumes
  intact.

**Test:** verificatie-script `klai-infra/scripts/verify-cleanup-timer.sh`.

## AC-5 — Audit-stream queryable in VictoriaLogs (UNION-check)

- **Given** REQ-5 is uitgevoerd EN AC-2 hold (tenant-LibreChats
  hebben labels),
- **When** een VictoriaLogs query
  `service:klai-orphan-audit AND _time:[now-7d,now]` wordt gedraaid,
- **Then** ten minste één event verschijnt (audit_run_completed),
  EN ZERO `event:orphan_no_managed_label` events voor running
  productie-containers — omdat alle prod-containers nu OF
  `com.docker.compose.project=klai-core` OF
  `klai.managed_by=portal-api-provisioning` dragen, of een bewust
  `klai.adhoc=*` opt-in.

**Test:** VictoriaLogs MCP query of curl.

## AC-6 — Audit-events worden door REQ-1 hook gebruikt (klasse-aware)

- **Given** AC-5 holdt EN REQ-1 hook raadpleegt VictoriaLogs in
  Check 4,
- **When** een Claude-sessie probeert
  `docker rm <container-met-recent-orphan-event>` te draaien (een
  container die de audit-script heeft geflagd als label-loos),
- **Then** de hook detecteert het orphan-event én blokkeert met
  bericht dat naar de query-resultaten verwijst. EN: voor een
  legitieme klasse-B container (drie labels aanwezig) genereert de
  audit GEEN orphan-event, dus de hook valt terug op tenant-pattern
  check (die alsnog blokkeert) — met diagnose "klasse B managed
  container — use deprovision flow".

**Test:** end-to-end test op dev-stack met dummy container scenarios.

## AC-7 — Pitfall + CI-guard aanwezig

- **Given** REQ-7 + REQ-2c zijn uitgevoerd,
- **When** in deze repo
  `grep -q "container-cleanup-without-preflight"
  .claude/rules/klai/pitfalls/process-rules.md` draait, EN in
  klai-infra CI op een PR die expliciet een nieuwe `librechat-X` zonder
  klasse-B labels introduceert (via een test-fixture die _start_librechat_container
  bypassed),
- **Then** grep returneert exit-0, EN klai-infra CI faalt op de
  `audit-compose-orphans` step.

**Test:** grep-step in klai CI; expliciete regression-fixture in
`test-audit-compose-orphans.sh`.

## Run Acceptance Aggregate

De SPEC is **acceptabel** wanneer:

- AC-1 t/m AC-7 allemaal hold binnen 7 dagen na laatste deploy.
- Geen productie-incident veroorzaakt door SPEC-deploys.
- 30 dagen post REQ-6 go-live: dangling-image count consistent <50.
- 30 dagen post REQ-1 go-live: minstens 1 `event:hook_blocked` in
  VictoriaLogs.
- 30 dagen post REQ-2 + REQ-5 go-live: zero
  `event:orphan_no_managed_label` events buiten gelabelde
  `klai.adhoc=*` containers.
- librechat-voys draagt klasse-B labels en kan niet meer per ongeluk
  als wees worden weggegooid (AC-1 + AC-2 combinatie).
- Toekomstige tenant-provisioning trajectoires landen automatisch met
  juiste labels (REQ-2a code-fix is in `_start_librechat_container`).

## Wat dit NIET test

- Server-side handmatige `ssh core-01 → docker rm` zonder Claude.
  REQ-5 audit is detectie-vangnet, niet preventie.
- Image-pinning compliance (geschrapt in v0.2.0 SPEC; canoniek in
  VERSIONS.md).
- Volume-data-integriteit. REQ-6 raakt geen volumes.
- Hook werking voor andere agents (niet Claude Code). REQ-5 audit is
  detectie-vangnet voor alle herkomst-paden.
