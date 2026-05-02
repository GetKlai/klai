# SPEC-INFRA-CONTAINER-HYGIENE-001 — Acceptance Criteria (v0.2.0)

Zeven Given/When/Then scenarios. Elke AC is mechanisch verifieerbaar
— geen mens-leesbaar rapport om te beoordelen, geen "agent zou X
moeten doen". Alle checks via tool-output, exit-codes, query-resultaten.

## AC-1 — Pre-tool hook blokkeert destructieve docker-acties

- **Given** REQ-1 is uitgevoerd (`.claude/hooks/klai/container-hygiene-preflight.sh`
  bestaat, `.claude/settings.json` heeft de PreToolUse-hook),
- **When** een Claude-sessie probeert een Bash tool-call met commando
  `docker rm librechat-voys` te draaien,
- **Then** de tool-call returneert exit-code 1 met output bevattende
  het woord "BLOCKED" (door één van de vijf checks geactiveerd —
  vermoedelijk tenant-naam check), EN de container is NIET verwijderd
  (verifieerbaar via `docker ps --filter name=librechat-voys`).

**Test:** test-fixture met dummy `docker rm` poging, exit-code-assertie.
Plus drie negatieve cases (`docker logs`, `docker ps`, `docker exec`)
moeten exit-0 returnen — hook mag legitieme commando's niet blokkeren.

## AC-2 — librechat-voys is compose-managed met juiste labels

- **Given** REQ-2c PR is gemerged en de klai-infra deploy-workflow heeft
  via `compose-up.sh` (REQ-3, of via het pre-REQ-3 pad indien REQ-3
  nog niet uitgerold) op core-01 uitgevoerd,
- **When** een Bash-command op core-01 draait
  `docker inspect librechat-voys --format '{{index .Config.Labels
  "com.docker.compose.project"}}'`,
- **Then** de output is exact `klai-core` (niet leeg), EN
  `docker inspect librechat-voys --format '{{index .Config.Labels
  "com.docker.compose.service"}}'` returneert `librechat-voys`.

**Test:** post-deploy verificatie-script in REQ-2c PR-body.

## AC-3 — Deploy-wrapper + `--remove-orphans` werkt mechanisch

- **Given** REQ-3 is uitgevoerd (alle 10 workflows roepen
  `compose-up.sh` aan) EN REQ-2c is reeds gemerged + gedeployed,
- **When** een opzettelijke "wees-test" wordt uitgevoerd: een test-
  service `compose-orphan-test` wordt toegevoegd aan een
  test-compose-bestand, gedeployd via dezelfde pipeline, dan uit het
  compose-bestand verwijderd, en opnieuw gedeployd via
  `compose-up.sh`,
- **Then** na de tweede deploy is `compose-orphan-test` niet meer
  running (`docker ps --filter name=compose-orphan-test --format
  '{{.Names}}'` returns leeg), EN `librechat-voys` is NOG STEEDS
  running (omdat hij compose-gedeclareerd is — anders zou `--remove-orphans`
  hem ook hebben weggegooid).

**Test:** scripted regression-test in `klai-infra/scripts/test-deploy-wrapper.sh`,
runs op core-01 staging-equivalent of direct via dummy compose-file.

## AC-4 — Daily safe cleanup timer draait succesvol

- **Given** REQ-6 is uitgevoerd (`docker-cleanup.timer` is enabled +
  active op core-01),
- **When** 48 uur is verstreken sinds installatie en
  `journalctl -u docker-cleanup.service --since '48h ago'` wordt
  geraadpleegd,
- **Then** ten minste 2 successful runs zijn zichtbaar (één per dag),
  elk met exit-code 0, EN `docker images --filter dangling=true -q
  | wc -l` returneert <50, EN `docker volume ls -q` returneert dezelfde
  set named volumes als 48u geleden (geen volume-prune is uitgevoerd
  — verifieerbaar via diff van pre/post lijsten).

**Test:** verificatie-script `klai-infra/scripts/verify-cleanup-timer.sh`
in REQ-6 PR.

## AC-5 — Audit-stream is queryable in VictoriaLogs

- **Given** REQ-5 is uitgevoerd (`orphan-audit.timer` is enabled +
  active),
- **When** de eerste zondag-03:00 voorbij is en een VictoriaLogs query
  `service:klai-orphan-audit AND _time:[now-7d,now]` wordt gedraaid,
- **Then** ten minste één event verschijnt (zelfs als het
  `event:audit_run_completed` met `severity:info` is om aan te tonen
  dat de audit succesvol draaide), EN er zijn nul `event:orphan_no_compose_label`
  events (omdat na REQ-2c alle prod-containers compose-managed zijn,
  modulo gelabelde `klai.adhoc=*` containers die in een eigen
  event-type vallen).

**Test:** VictoriaLogs MCP query van Claude-sessie of handmatig curl
naar VictoriaLogs API. Eerste handmatige run als CI-validatie tijdens
REQ-5 PR.

## AC-6 — Audit-events zijn AI-bruikbaar in cleanup-context

- **Given** AC-5 holdt (audit-stream is actief), EN REQ-1 hook
  raadpleegt de stream als deel van check 5 (recent-traffic),
- **When** een Claude-sessie probeert
  `docker rm <een-container-met-recente-orphan-events>` te draaien,
- **Then** de hook detecteert het orphan-event in VictoriaLogs en
  blokkeert met een specifiek diagnose-bericht dat naar de
  query-resultaten verwijst (bijv. "BLOCKED: target had 3 orphan_no_compose_label
  events in last 7d — manual review required").

**Test:** end-to-end test met dummy container die handmatig wordt
gestart zonder labels, audit detecteert hem in volgende run, daarna
poging tot `docker rm` wordt geblokkeerd door de hook met de juiste
boodschap.

## AC-7 — Pitfall-documentatie + CI-guard aanwezig

- **Given** REQ-7 + REQ-2a zijn uitgevoerd,
- **When** in deze repo
  `grep -q "container-cleanup-without-preflight"
  .claude/rules/klai/pitfalls/process-rules.md` draait, EN in
  klai-infra CI op een PR die expliciet `librechat-voys` zonder
  compose-block introduceert,
- **Then** de grep returneert exit-0 (pitfall aanwezig), EN de
  klai-infra CI faalt op de `audit-compose-orphans` step met een
  duidelijke foutmelding ("librechat-voys staat in Caddyfile maar
  niet in docker-compose.yml").

**Test:** in deze repo via CI grep-step. In klai-infra via een
expliciete regression-fixture in `test-audit-compose-orphans.sh` die
exact deze faal-case set-up.

## Run Acceptance Aggregate

De SPEC is **acceptabel** wanneer:

- AC-1 t/m AC-7 allemaal hold binnen 7 dagen na laatste deploy.
- Geen productie-incident veroorzaakt door SPEC-deploys.
- 30 dagen post REQ-6 go-live: dangling-image count consistent <50
  (bewijst structurele werking van daily prune naast de bewust
  geaccepteerde `:latest`-rolling-tags op klai-eigen images).
- 30 dagen post REQ-1 go-live: ten minste 1 reële `event:hook_blocked`
  event in VictoriaLogs (bewijst dat hook in productie minstens één
  cleanup-poging mechanisch heeft gevangen).
- 30 dagen post REQ-5 go-live: zero `event:orphan_no_compose_label`
  events buiten gelabelde `klai.adhoc=*` containers (bewijst dat
  REQ-2 alles-via-compose policy mechanisch wordt gehandhaafd).
- librechat-voys recovery-werk is teruggebracht tot compose-managed
  first-class service en kan niet meer per ongeluk wees-zijn (AC-2 +
  AC-3 combinatie).

## Wat dit NIET test

- Server-side handmatige `ssh core-01 → docker rm` zonder Claude:
  REQ-1 hook werkt alleen in Claude Code-context. Voor SSH is REQ-5
  audit het detectie-vangnet, niet preventie.
- Image-pinning compliance: geschrapt in v0.2.0; valt onder
  `docs/runbooks/version-management.md` audit, niet deze SPEC.
- Volume-data-integriteit: REQ-6 raakt geen volumes; volume-cleanup
  blijft handmatig en out-of-scope.
- Hook werking voor andere agents (niet Claude Code): niet getest;
  REQ-5 audit is detectie-vangnet voor alle herkomst-paden.
