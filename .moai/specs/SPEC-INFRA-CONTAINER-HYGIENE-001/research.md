# SPEC-INFRA-CONTAINER-HYGIENE-001 — Research

Diep onderzoek vóór de SPEC. Findings uit de live core-01 staat per
2026-05-02, de klai-infra deploy-workflows, en het librechat-voys
incident dat de aanleiding voor deze SPEC is.

## 1. Het librechat-voys incident (root cause walk-through)

**Tijdlijn 2026-05-02:**

- `docker ps` op core-01 toonde 47 containers, waaronder `librechat-voys`
  met image `62043e990607` (untagged sha), Up 9 dagen.
- `docker inspect librechat-voys --format '{{.Config.Labels}}'` returnde
  een lege map. Géén `com.docker.compose.project=klai-core` label, geen
  `com.docker.compose.service`, geen container-number.
- `grep librechat /opt/klai/docker-compose*.yml` toonde alleen
  `librechat-dev` (dev compose) en `librechat-getklai` (prod compose) als
  service-blocks. `librechat-voys` was géén compose-service.
- `docker exec klai-core-caddy-1 cat /etc/caddy/Caddyfile | grep -i 'voys\|chat-voys'`
  returnde niets — Caddy routeerde niet naar deze container.
- Cleanup-besluit baseerde zich uitsluitend op (a) geen compose-label en
  (b) geen Caddy-route → "wees, weg ermee". Container + image (samen met
  60 GB aan dangling images van CI-deploys) verwijderd.
- Een parallelle Claude-sessie (PR #265-context) ontdekte daarna dat de
  voys-tenant chat-route stuk was. Conclusie van die sessie: librechat-voys
  "bestond niet — nooit geprovisioneerd voor het voys tenant", wat klopte
  na de cleanup. Of het ervóór wèl correct routeerde via een ander
  Caddy-mechanisme (subpath rewrite, on-demand TLS) of al een tijd
  losgekoppeld was, is achteraf niet meer met zekerheid vast te stellen
  — de logs voor de afgelopen 9 dagen die door deze container zouden
  moeten zijn afgehandeld zijn niet in VictoriaLogs gequeried.
- Recovery: `docker run -d --name librechat-voys --restart unless-stopped`
  met `ghcr.io/danny-avila/librechat:v0.8.5-rc1` (zelfde image als
  `librechat-getklai`) en mounts naar `/opt/klai/librechat/voys/`
  (env-file en `librechat.yaml` waren intact gebleven; alleen container +
  image waren weg).

**Vijf root-causes uit dit incident:**

1. Productie-container draaide via een handmatige `docker run` zonder
   compose-declaratie. Gevolg: geen `com.docker.compose.project` label →
   ontoeschrijfbaar tegen automatisering.
2. Image was een lokaal aanwezige sha (`62043e9...`) zonder registry-tag.
   Gevolg: niet reproduceerbaar, niet rollback-baar via GHCR pull, en
   automatisch dangling bij elke `image prune`.
3. Caddy had geen actieve route. Detectie zou trivial zijn geweest: `for
   service in $(docker compose config --services); do grep $service
   /etc/caddy/Caddyfile; done`. Bestaat niet als check.
4. Geen pre-cleanup checklist. Cleanup-agent (in dit geval ik) reageerde
   op "geen compose label" alsof dat een definitieve wees-marker was —
   het is een sterk signaal maar geen bewijs. De tenant-specifieke naam
   (`-voys`) had moeten triggeren.
5. Geen periodieke detectie. Een wekelijks audit-rapport had de
   onbeheerde container al weken geleden geflagd voor menselijke review.

## 2. Inventaris core-01 (post-cleanup snapshot 2026-05-02)

**Running:** 44 containers (was 47, na cleanup van `beautiful_napier`,
`librechat-voys` (hersteld), `klai-core-glitchtip-migrate-1` exited).

**Categorisatie naar herkomst:**

| Categorie | Aantal | Risico |
|---|---|---|
| `klai-core` compose project (labels OK) | 41 | Laag |
| Compose-service in `docker-compose.override.yml` (labels OK) | 1 (`librechat-getklai`) | Laag |
| Compose-service in `docker-compose.dev.yml` (labels OK) | 1 (`librechat-dev`) | Laag |
| Handmatige `docker run` (géén compose-label) | 1 (`librechat-voys`, post-recovery) | **HIGH** |

`librechat-voys` is na recovery nog steeds zonder compose-label —
het werd hersteld via een handmatige `docker run` om continuïteit te
garanderen. Tot REQ-2 van deze SPEC is uitgevoerd, blijft het de enige
container in deze categorie en de enige wees in deze inventaris.

**Disk:** 104 GB / 25% (van 347 GB / 84% pre-cleanup; -243 GB winst).

**Volumes:** 23 actief (was 33), 22 in gebruik. `klai-core_vexa-recordings-data`
(58 MB, recordings van 23 maart, geen schrijven sinds Vexa 0.10 update
op 19 april) bewust niet aangeraakt — klantdata in ruwe vorm, vraagt
eigen evaluatie buiten deze SPEC.

**Dangling images:** 0 (post-cleanup). Pre-cleanup waren het er 679,
gemiddeld 1.0–1.3 GB per stuk → 60 GB. Bron: elke deploy van een
`:latest`-getagde image bumpt de tag, vorige image wordt
`<none>:<none>`.

## 3. Deploy-workflow inventaris (.github/workflows/)

**21 workflow-bestanden totaal in klai (private):**

```
alerting-check.yml         klai-mailer.yml          renovate.yml
audit-compose.yml          knowledge-ingest.yml     retrieval-api.yml
caddy.yml                  litellm-hook-deploy.yml  scan-pinned-images.yml
deploy-compose.yml         portal-api.yml           scribe-api.yml
deploy-librechat-config.yml portal-frontend.yml     semgrep.yml
docs.yml                   whisper-server.yml       env-scope-guard.yml
klai-connector.yml         klai-knowledge-mcp.yml   zitadel-oidc-drift.yml
```

**Workflows met `docker compose up` of `docker-compose up` (10 stuks):**

`caddy.yml`, `docs.yml`, `klai-connector.yml`, `klai-knowledge-mcp.yml`,
`klai-mailer.yml`, `knowledge-ingest.yml`, `portal-api.yml`,
`retrieval-api.yml`, `scribe-api.yml`, `whisper-server.yml`.

**Workflows met `--remove-orphans`:** **0**. Bevestigd via grep —
geen enkele service-deploy gebruikt deze flag. Gevolg: zodra een service
uit `/opt/klai/docker-compose.yml` wordt verwijderd, blijft de oude
container draaien tot iemand 'm handmatig stopt.

**Reeds bestaande hygiene-workflows (pre-bestaand):**

- `audit-compose.yml`: bestaat. Inhoud nog niet gelezen — kan overlappend
  doel hebben.
- `scan-pinned-images.yml`: bestaat. Naam suggereert image-pinning audit
  (REQ-4 territorium).
- `env-scope-guard.yml`: bestaat. Recent (SPEC-SEC-ENVFILE-SCOPE-001) —
  guard tegen env-file-scope-bleed.

Vóór REQ-3/REQ-4 implementatie deze drie workflows volledig lezen om
overlap of conflict met bestaande tooling te vermijden. **Belangrijk:**
`scan-pinned-images.yml` zou kunnen betekenen dat REQ-4's beleidsdoel
(`:latest` weren) al deels bestaat — dat zou de scope van REQ-4 wezenlijk
veranderen.

## 4. Compose image-tagging audit (huidige staat per 2026-05-02)

`grep -E 'image:.*ghcr.io/getklai|image:.*klai/' /opt/klai/docker-compose.yml`
op core-01 (sample uit `docker ps`):

| Service | Tag in compose | Tag in registry | Pin status |
|---|---|---|---|
| portal-api | `:latest` | overschreven elke deploy | NIET-gepind |
| klai-connector | `:latest` | overschreven elke deploy | NIET-gepind |
| knowledge-ingest | `:latest` | overschreven elke deploy | NIET-gepind |
| klai-knowledge-mcp | `:latest` | overschreven elke deploy | NIET-gepind |
| klai-mailer | `:latest` | overschreven elke deploy | NIET-gepind |
| scribe-api | `:latest` | overschreven elke deploy | NIET-gepind |
| retrieval-api | `klai/retrieval-api:local` | lokaal gebouwd | Niet-applicabel |
| caddy | `ghcr.io/getklai/caddy-hetzner:latest` | overschreven elke deploy | NIET-gepind |
| docs-app | `ghcr.io/getklai/klai-docs:latest` | overschreven elke deploy | NIET-gepind |
| librechat-getklai | `ghcr.io/danny-avila/librechat:v0.8.5-rc1` | upstream stable | Gepind (third-party) |
| vexa stack (runtime-api etc.) | `vexaai/runtime-api:0.10.0-260419-1129` | upstream pinned | Gepind (third-party) |

**Bevinding:** géén klai-eigen service gebruikt versie-pinning. Élke
deploy van een ghcr.io/getklai-image overschrijft `:latest` → vorige
image wordt dangling. Dit is dé bron van de 679 dangling images / 60 GB.

## 5. Caddy-routing audit

`docker exec klai-core-caddy-1 cat /etc/caddy/Caddyfile | grep -E '^[a-z]' | head -30`
toonde een list reverse-proxy-blocks per hostname. Voor de SPEC relevant:

- Hostnames in Caddyfile zonder bijbehorende running container = orphan
  routing-rules (downtime risico).
- Container-namen die in `docker ps` voorkomen maar nergens als
  upstream in Caddyfile staan = potentieel verlaten of bypass-routing.

Dit is precies de check die mij vandaag gered had: een diff tussen
`docker compose ps` en `caddy upstreams` zou `librechat-voys` als
"draait, maar geen Caddy-route" hebben getoond — een rode vlag voor
een tenant-specifieke container.

## 6. Patterns uit andere klai-infra repos

`klai-infra` (apart repo) bezit `/opt/klai/docker-compose.yml`, SOPS env
files, en de deploy-workflows. Uit de pitfalls in
`.claude/rules/klai/pitfalls/process-rules.md`:

- **`scribe-deploy-no-alembic`**: scribe-api deploy doet `docker compose
  up -d` zonder migrate-step. Pattern: `docker compose up -d` is rauw,
  zonder hygiene flags.
- **`env-file-migration-reverse-check`**: env-files migratie (SOPS) heeft
  een audit-discipline. Container-hygiene heeft die nog niet.
- **`spec-work-in-a-worktree`**: SPEC-werk in dedicated worktree. Voor
  REQ-2 (compose-edit voor librechat-voys) geldt dit ook — klai-infra
  worktree.

## 7. Industry standard (uit web research van 2026-05-02 in deze sessie)

**Bron-consensus voor single-server Docker prod (jullie context):**

- `docker image prune -f` (alleen dangling) is **100% safe**, dagelijks
  uitvoeren is breed aanbevolen.
- `docker volume prune` automatisch is unanimous **af te raden**:
  dataverlies-risico (named volumes met data, edge-case label-detection).
- **Versie-pinning** (geen `:latest`) is de #1 maatregel die de
  dangling-explosie structureel stopt. Alle bronnen noemen dit als
  "advanced but essential" voor multi-deploy-per-day setups.
- Tooling: `Spotify/docker-gc` is officieel **inactive**. Native
  `systemd timer` is moderne keuze voor schedule (vs cron) — logging via
  `journalctl`, geen extra container nodig.

Bronnen: oneuptime, Alex Gallacher, Conan Mercer, Docker Docs (build cache GC),
Designcise. Geen bron pleitte voor automatische `volume prune` of
agressieve `image prune -af` zonder retention-filter.

## 8. Reference implementations in klai-codebase

**Voor REQ-1 (HARD rule file):** patroon volgen van bestaande klai-rules:

- `.claude/rules/klai/no-ask-user-question.md` (kort, dwingend, één
  pagina) — toon-template voor REQ-1.
- `.claude/rules/klai/pitfalls/process-rules.md` (lijst van anti-patterns
  met prevention-secties) — sectie-template voor de pre-cleanup
  checklist body.
- `.claude/rules/klai/infra/observability.md` (hoe-doen-we-X reference) —
  toon-template voor de container-hygiene how-to.

**Voor REQ-5 (audit-script):** volg het pattern van
`scripts/victorialogs-tunnel.sh` (bash, single-file, idempotent,
documenteerbaar via `--help`).

**Voor REQ-6 (systemd timer):** geen bestaand voorbeeld in de klai-repo.
Standaard systemd-pattern via `/etc/systemd/system/{naam}.service` +
`.timer`. Operator-action via SSH naar core-01.

## 9. Risico's en open vragen

**Risico's voor deze SPEC:**

- REQ-2 (librechat-voys compose-block) raakt `/opt/klai/docker-compose.yml`
  in klai-infra, een aparte repo. PR-coordination nodig — niet ìn deze
  klai repo. Documenteer als hand-off.
- REQ-3 (`--remove-orphans` toevoegen) heeft als bijwerking dat
  `librechat-voys` BIJ DE EERSTE deploy zou worden verwijderd indien
  REQ-2 niet eerst gemerged is. Strikte volgorde nodig: REQ-2 → REQ-3.
- REQ-4 (image-pinning pilot voor portal-api) raakt de portal-api
  GitHub workflow én klai-infra `.env` voor de versie-var. Cross-repo
  change met deploy-implicaties — stage-gates nodig (zie
  `validator-env-parity` pitfall).
- REQ-6 (systemd timer) draait als root op core-01. Operator-action via
  SSH. Geen enkele klai-repo file beweegt — de timer-installatie zelf
  is buiten Git, alleen het gegenereerde unit-bestand wordt in
  `klai-infra` gecheckt-in onder `core-01/systemd/`.

**Open vragen vóór `/moai run`:**

1. Wil de klai-infra deploy-pijplijn de systemd timer files *zelf*
   provisionen (Ansible / shell script in deploy-compose.yml)? Of is het
   handmatig via SSH? Antwoord stuurt waar het unit-bestand woont.
2. Is `audit-compose.yml` workflow al een orphan-detectie tool? Zo ja,
   uitbreiden ipv nieuwe scripts/`docker-orphan-audit.sh` script.
3. Is `scan-pinned-images.yml` workflow al actief op image-pinning? Zo ja,
   REQ-4 wordt veel kleiner — alleen de pilot uitvoeren ipv beleid
   formaliseren.

Beide vragen kunnen tijdens `/moai run` Phase 1 (manager-strategy) worden
beantwoord door de drie workflows ECHT te lezen voor REQ-3 en REQ-4
implementatie begint.

## 10. Conclusie van research (initiële versie v0.1.0)

De SPEC raakt drie lagen:

1. **Beleid** (rules-files in deze repo) → REQ-1, REQ-2-policy.
2. **Tooling** (scripts en systemd-timer op core-01) → REQ-5, REQ-6.
3. **Cross-repo deploy-discipline** (klai-infra workflows + compose) →
   REQ-2-implementation, REQ-3, REQ-4.

De volgorde-discipline tussen lagen is kritiek: REQ-2 MOET vóór REQ-3,
anders wist `--remove-orphans` de net-herstelde container. REQ-1 en
REQ-6 hebben geen externe afhankelijkheden en kunnen direct.

## 11. Bestaande version-management infrastructure (v0.2.0 herziening)

Tijdens v0.1.0 review ontdekte de gebruiker dat klai al een uitgebreid
version-management systeem heeft. Twee bestanden zijn de canoniek:

**`deploy/VERSIONS.md`** — complete inventory van alle gepinde external
images met rationale per stuk. Sleutelstatement:

> Each CI workflow also tags the build with `:${github.sha}` so rollbacks
> are possible via explicit SHA pin. These are NOT production `:latest`
> anti-patterns — they are continuous-deployment rolling tags owned by
> our own CI pipelines.

Dit zegt expliciet dat `:latest` op `ghcr.io/getklai/*` **bewust beleid
is**, niet een fout om te repareren. Rollback gebeurt via
`docker pull <image>:<sha> && tag :latest && compose up -d`.

**`docs/runbooks/version-management.md`** — 514-regelige playbook met:

- §1 Pinning principles (lockfiles mandatory, no `:latest` for external)
- §2 Cadence (Renovate Mon 05:00, security CVE bumps immediate)
- §3 Procedures per upgrade type (Python pkg, Node pkg, Docker minor,
  Docker major, Python runtime, Node runtime)
- §4 Testing gates per type
- §5 Pinning rules cheat sheet
- §6 Rollback procedures (fast + with data corruption)
- §7 Eight historical pitfalls inclusief CRIT FalkorDB graph-loss
- §8 Tools reference (uv, npm, Docker, registry queries, CI automation)
- §9 CVE detection layers (5 mechanismes, defense-in-depth)
- §10 Quarterly audit procedure
- §11 Stateful service change checklist
- §12 When this playbook is wrong (judgment guidance)

**Bestaande automatisering:**

- `.github/workflows/scan-pinned-images.yml` — wekelijkse Trivy scan
- `.github/workflows/audit-compose.yml` — bestaat met
  `scripts/audit-compose-volumes.sh` + `scripts/test-audit-compose.sh`
- `deploy/check-image-tags.sh` — pre-commit voor Vexa convention
- Renovate + Dependabot — auto-PRs voor updates
- Trivy per service workflow — CVE-scan op build

**Impact op deze SPEC:**

REQ-4 (image-pinning pilot voor portal-api) **is dubbel werk en gaat
in tegen bestaand beleid**. Geschrapt in v0.2.0. De 679 dangling images
zijn een geaccepteerde bijwerking van `:latest`-rolling-tags, en de
fix is daily prune (REQ-6) — niet pinning forceren.

## 12. AI-first uitgangspunt (v0.2.0 herziening)

Tijdens v0.1.0 review wees de gebruiker erop dat de SPEC niet uitgaat
van de werkelijkheid: een AI doet het primaire code- en deploy-werk.
"Vertrouw op menselijke discipline" is dan geen vangnet maar een gat.

Vertaling per requirement van mens-first → AI-first:

| Requirement | Mens-first (v0.1.0) | AI-first (v0.2.0) |
|---|---|---|
| REQ-1 | Markdown-checklist die agent moet lezen | Bash-script + PreToolUse hook in `.claude/settings.json` die `docker rm`/`rmi`/`volume rm` mechanisch blokkeert tot script exit-0 |
| REQ-2 | "Beleid: alles via compose" | CI-guard in `audit-compose.yml`: PR met handmatig docker-run-pattern faalt; post-deploy verifier flagt elke container zonder `com.docker.compose.project=klai-core` label |
| REQ-3 | Per-workflow `--remove-orphans` toevoegen aan 10 files | Eén deploy-wrapper `deploy/scripts/compose-up.sh`; alle workflows roepen de wrapper aan; flag zit ingebouwd, niet vergetelijk |
| REQ-5 | Weekly markdown-rapport voor menselijke review | Audit-script schrijft structlog-events naar VictoriaLogs (`service:klai-orphan-audit`, `event:orphan_detected`); Grafana dashboard panel + alert; AI queryt via VictoriaLogs MCP vóór elke cleanup-beslissing |
| REQ-6 | Systemd timer | Blijft (was al mechanisch) |
| REQ-7 | Markdown pitfall | Blijft als secondary; primair vangnet is REQ-1 hook |

Het verschil: v0.1.0 was "leer de AI beter te zijn"; v0.2.0 is "AI kan
de fout niet meer maken, ook bij slechte dag of bij een andere agent".

**Hooks-mechanisme (REQ-1):**

`.claude/settings.json` ondersteunt `PreToolUse` hooks per tool.
Voorbeeld:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash $CLAUDE_PROJECT_DIR/.claude/hooks/klai/container-hygiene-preflight.sh"
          }
        ]
      }
    ]
  }
}
```

De hook ontvangt het commando dat Claude wil draaien via stdin als JSON.
Het script parseert eruit `docker rm/rmi/volume rm/system prune` en
draait dán de checklist; exit-code != 0 = blocked. Werkt voor élke
Claude-sessie in deze repo, mechanisch.

**Beperking:** hooks gelden alleen voor Claude Code. Een operator die
direct via SSH `docker rm` doet wordt niet afgevangen. Daarvoor is
REQ-5 (audit-stream + Grafana alert) het detectie-vangnet achteraf.

**`product_events` versus VictoriaLogs voor REQ-5:**

`product_events` is voor user-facing business events (signups,
billing, meetings). Cleanup is een ops-event, hoort in VictoriaLogs
(structlog) zoals alle service-logs van klai. Resultaat:

- Audit-script logt via `structlog` met `service=klai-orphan-audit`
- VictoriaLogs MCP server kan AI queryen: `service:klai-orphan-audit
  AND event:orphan_detected AND _time:[now-7d,now]`
- Grafana Logs-panel + alert via dezelfde stream

Geen DB-schema verandering nodig. Past in bestaande observability stack
(Alloy → VictoriaLogs).

**Bestaande `audit-compose.yml` voor REQ-2:**

Workflow bestaat al, audit alleen nu volume-mounts (`audit-compose-volumes.sh`).
REQ-2 breidt 'm uit met een `audit-compose-orphans.sh` check, gekoppeld
aan dezelfde workflow-trigger. Pattern volgt bestaande
`test-audit-compose.sh` regression-test conventie.
