# SPEC-INFRA-CONFIG-SYNC-001 — Research

## 1. Context: closure van een latente klasse

SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 (gemerged 2026-05-07,
`a2a090a5`) sloot het Caddyfile-sync gat. Direct na merge bleek bij
audit van `deploy/docker-compose.yml` dat hetzelfde patroon nog 3
keer onbeveiligd zit:

```bash
$ grep -E "^\s+- \./" deploy/docker-compose.yml | sort -u
      - ./alloy/config.alloy:/etc/alloy/config.alloy:ro            # gat
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile                      # net dichtgemaakt
      - ./firecrawl-nuq-init.sql:/docker-entrypoint-initdb.d/...   # one-shot init
      - ./grafana/provisioning:/etc/grafana/provisioning:ro         # gesynct (SPEC-OBS-001)
      - ./librechat/getklai/.env                                   # eigen workflow
      - ./librechat/getklai/images:...                             # eigen workflow
      - ./librechat/getklai/librechat.yaml:...                     # eigen workflow
      - ./librechat/patches/format.cjs:...                         # eigen workflow
      - ./librechat:/librechat                                      # eigen workflow
      - ./litellm/config.yaml:/app/config.yaml:ro                  # eigen workflow
      - ./litellm/custom_router.py:...                             # eigen workflow
      - ./litellm/klai_*.py:...                                    # eigen workflow
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/...        # one-shot init
      - ./searxng/settings.yml:/etc/searxng/settings.yml:ro        # gat
      - ./vexa/profiles.yaml:/app/profiles.yaml:ro                  # gat
```

Drie gaten:
- `alloy/config.alloy` — log-pipeline config.
- `searxng/settings.yml` — search-engine config.
- `vexa/profiles.yaml` — meeting-bot profielen.

Workflow-trigger audit:

```bash
$ for path in alloy/config.alloy searxng/settings.yml vexa/profiles.yaml; do
    grep -lr "deploy/$path" .github/workflows/ 2>/dev/null
  done
# alloy: niets
# searxng: niets
# vexa: niets
```

Alle drie zonder eigen sync-workflow.

## 2. Compose service-namen — asymmetrie bij vexa

Bind-mount → compose-service mapping:

| Bind-mount | Compose service |
|---|---|
| `./alloy/config.alloy` | `alloy` |
| `./caddy/Caddyfile` | `caddy` |
| `./searxng/settings.yml` | `searxng` |
| `./vexa/profiles.yaml` | **`runtime-api`** (niet `vexa`) |

Bevestigd via `awk` parse van compose:
```bash
awk '/^  [a-zA-Z0-9_-]+:$/{svc=$1; sub(":", "", svc)}
     /\.\/(alloy|searxng|vexa)/{print svc, "→", $0}' deploy/docker-compose.yml
# alloy →   - ./alloy/config.alloy:/etc/alloy/config.alloy:ro
# searxng → - ./searxng/settings.yml:/etc/searxng/settings.yml:ro
# runtime-api → - ./vexa/profiles.yaml:/app/profiles.yaml:ro
```

De helper accepteert daarom de compose-service-naam als expliciete
parameter — niet automatisch afgeleid van de bind-mount-path.

## 3. Reload semantiek per service

| Service | Hot-reload mogelijk? | Klai-keuze |
|---|---|---|
| caddy | Nee — `admin off` blokkeert reload via Admin API | container-recreate (~1s TLS-onderbreking) |
| alloy | Ja — HTTP `/-/reload` of SIGHUP signaal | container-recreate (~2-3s observability-blackout, simpler in CI) |
| searxng | Nee — settings.yml gelezen bij startup | container-recreate (geen alternatief) |
| runtime-api (vexa) | Onbekend / niet onderzocht | container-recreate (universeel werkend) |

Conclusie: container-recreate is de gemeenschappelijke noemer. Het
vereenvoudigt de helper en de mental-model. Als toekomstige tuning
hot-reload voor één service nodig blijkt (bijv. alloy moet onder een
zware log-load niet herstarten), kan dat per-service als follow-up
SPEC. Niet voorbarig optimaliseren.

## 4. Helper-design overwegingen

### 4.1 Bash function vs inline copies

Vier inline kopieën van het sync-block (~30 regels per stuk = 120
regels) zou een onleesbare diff zijn voor reviewers. Eén helper +
4 calls = ~50 regels totaal en de calls zijn 3-veld-tabellen (svc /
src / dst). Reviewer kan in één oogopslag valideren.

### 4.2 Bash function placement

Helper definitie binnen het ssh-action `script:` blok, na `set -e`,
vóór de eerste call. Functie is local in de bash-sessie van de
SSH-action — geen state-verlies, geen externe import.

### 4.3 Variable scoping

`local svc="$1"` etc. om name-clashes te voorkomen met outer-scope
variabelen (de bestaande `RSYNC_PROV_CHANGES`, `SMOKE_EXIT`, etc.
elders in het script). `local` is bash-only; werkt in
`appleboy/ssh-action`'s remote bash.

### 4.4 Error handling

`set -e` is al actief. Helper kan dus rsync of docker compose
faillures laten propageren naar workflow-fail. Voor de health check
loop: expliciete `exit 1` na 10s timeout, met log-dump er voor om
diagnose te versnellen.

### 4.5 Idempotency

`rsync -ac --itemize-changes`:
- `-a` archive mode (preserve metadata)
- `-c` content-checksum (negeert mtime; cruciaal voor fresh git
  clones met willekeurige mtime)
- `--itemize-changes` print één regel per file met change-flags

`grep -E '^[<>*]'` filtert change-indicators (`<` = transfer,
`>` = receive, `*` = special). Als geen matches: bestand was al
identiek → skip.

## 5. Out-of-scope: andere bind-mount klassen

### 5.1 Klasse A-dir (directory bind-mounts)

- `deploy/grafana/provisioning/` is een dir-rsync (`rsync -ac dir1/
  dir2/`) — andere flag-set dan single-file rsync. De helper zou twee
  paden moeten ondersteunen (file vs dir) of we hebben een tweede
  helper. Niet de moeite tot er een tweede dir-bind-mount bijkomt.

### 5.2 Klasse B (eigen deploy-workflows)

- `litellm-hook-deploy.yml` synct `deploy/litellm/*.{py,yaml}` naar
  `/opt/klai/litellm/`. Eigen contract; deze SPEC raakt het niet.
- `deploy-librechat-config.yml` synct
  `deploy/librechat/{patches,getklai/...}` naar core-01. Eigen
  contract.

Audit van die twee workflows op vergelijkbare gaps is een aparte SPEC
indien gewenst.

### 5.3 Klasse C (one-shot init)

- `deploy/postgres/init.sql` en `deploy/firecrawl-nuq-init.sql`
  worden alleen gelezen door Postgres bij volume-bootstrap. Eenmalige
  read; geen drift-risico in lopende productie. Migrations zijn de
  juiste tool voor schema-changes na bootstrap.

## 6. Risk model — eerste post-merge run

Bij de Caddyfile SPEC bleek de host-versie al byte-identiek aan main
HEAD (manueel-scp van diezelfde dag). Helper zou alleen drie nieuwe
"unchanged; skipping recreate." messages echoen plus de bestaande caddy
no-op. Geen recreates → geen downtime. Idempotent.

Mogelijk scenario: één van alloy/searxng/vexa op core-01 verschilt
toch met main HEAD (bijv. door een handmatige tweak die nooit naar de
repo gepusht is). Dan recreate die service. Verwacht impact:

| Service | Recreate-window | User-impact |
|---|---|---|
| alloy | ~2-3s | log-pipeline gat (logs geboekt na recreate; niets verloren omdat Alloy een persistent volume heeft) |
| searxng | ~1s | search-degradation, internal-only |
| runtime-api | ~3-5s | meeting-bot orchestrator API kort niet bereikbaar; actieve meetings raken niet geïnterrumpeerd (eigen containers per meeting) |

Geen catastrofale fail-modes. Acceptabel.

## 7. Sources

- `.github/workflows/deploy-compose.yml` (SPEC-INFRA-CADDY-CONFIG-
  DEPLOY-001 versie als startpunt)
- `deploy/docker-compose.yml` (bind-mount inventaris)
- `.claude/rules/klai/pitfalls/process-rules.md`
  `docker-compose-restart-vs-recreate (CRIT)`,
  `bind-mount-content-without-sync` (toe te voegen indirect via deploy.md
  in deze SPEC)
- SPEC-INFRA-CADDY-CONFIG-DEPLOY-001 (proof-of-concept, 1× pattern)
- SPEC-OBS-001 Phase C (grafana provisioning sync, dir-shape)
- Caddy 2 docs (`admin off` semantiek)
- Alloy docs (HTTP reload + SIGHUP support, niet gebruikt in Klai)
