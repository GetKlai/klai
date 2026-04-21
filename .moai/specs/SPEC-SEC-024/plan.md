# SPEC-SEC-024 — Implementation Plan

> Dit plan is uitsluitend planning. De daadwerkelijke implementatie gebeurt
> pas in `/moai run SPEC-SEC-024`.

---

## Aanpak in één alinea

We leveren in vier opeenvolgende milestones: (1) de audit zelf — een
reproduceerbare scan-tabel met verdicten, (2) een gereduceerde proxy-whitelist
in `deploy/docker-compose.yml` met per-verb rationale, (3) een CI-guard
(ast-grep of semgrep) die nieuwe `exec_run(` matches blokkeert, en (4) een
post-deploy smoke-test + documentatie-update. Milestone 1 is input voor 2,
3 en 4 — de audit-output is de bron-van-waarheid voor alle daaropvolgende
wijzigingen.

---

## Milestones (priority-gebaseerd, geen tijdsschattingen)

### M1 — Exhaustive audit (priority: HIGH, blokkerend voor M2–M4)

Doelen R1, R2, R3.

- **M1.1**: draai een mechanische scan met twee complementaire tools en
  consolideer:
  - `grep -rn "exec_run(" klai-portal/backend/` — breed, vangt strings
  - `ast-grep run -p 'exec_run($$$)' --lang py klai-portal/backend/` — AST,
    vangt alleen echte functie-calls (geen docstrings, geen comments)
  - Tevens: `grep -rn "client.images.build\|containers\.commit\|\.attach(\|\.top(\|volumes\.create\|volumes\.get" klai-portal/backend/`
- **M1.2**: consolideer in een tabel in dit plan (sectie "Audit-output" verderop)
  met kolommen: `file:regel`, `symbol`, `verdict` (fixed / never-ran /
  acceptable / legacy), `actie` (none / remove / refactor-to-protocol /
  add-to-allowlist), `rationale`.
- **M1.3**: vul een identieke tabel voor Docker API calls op de docker-py
  client die door de proxy *niet* zijn toegelaten — om te bewijzen dat we geen
  andere stille bom hebben.

**Deliverables M1**: volledig ingevulde tabellen in dit plan, gecommit op
branch `spec/SEC-024`. Geen code-wijzigingen in productie.

**Dependency voor M2–M4**: de verdicten in deze tabel bepalen welke verbs we
in M2 droppen.

### M2 — Proxy-whitelist reductie (priority: HIGH, agressief)

Doelen R4, R5, R6.

**Strategie**: pre-launch status = we kunnen het ons permitteren om te zien
wat breekt. Dus we droppen **elke** verb die geen aantoonbaar productie-pad
in portal-api raakt. Als runtime-api (black-box) stil iets nodig had,
verschijnt dat binnen minuten in de permanente alert (M4) en zetten we die
ene verb met één compose-edit terug. Dat is sneller en levert beter bewijs
op dan defensief laten staan.

- **M2.1**: vertaal de audit-tabel naar een per-verb rationale in deze plan
  (sectie "Verb rationale"). Elke `keep` moet minstens één verwijzing naar
  een gebruikt portal-api code-pad hebben; alles zonder portal-api-pad wordt
  `drop`, ongeacht vermoedens over runtime-api.
- **M2.2**: wijzig `deploy/docker-compose.yml:281-292`. Alleen de
  `environment:` keys onder `docker-socket-proxy` — géén wijzigingen aan
  het netwerk of de image-versie.
- **M2.3**: verifieer lokaal via `docker compose config docker-socket-proxy
  | grep -A 20 environment` dat de gewenste set eruit komt.
- **M2.4**: deploy via `deploy-compose.yml` GitHub Action (standaard flow,
  zie `.claude/rules/klai/infra/deploy.md` — CI-compose-sync).

**Deliverables M2**: minimaal compose-diff (alleen environment-keys),
rationale-tabel in dit plan, server-side verificatie.

**Guard voor regressie**: de smoke-test (M4) draait direct na deploy en
faalt hard als een `keep`-verb geblokkeerd raakt. De permanente alert-rule
(M4.2) vangt runtime-api-403's die pas bij een eerste bot-spawn zichtbaar
worden.

### M3 — CI-guard tegen reintroductie (priority: HIGH)

Doelen R7, R8.

- **M3.1**: voeg `sgconfig.yml` (repo root) + `rules/no-exec-run.yml` toe.
  Dit is de industry-standard ast-grep project layout die door de officiële
  `ast-grep/action@latest` GitHub Action verwacht wordt. Het eerdere schetsje
  met `.ast-grep/no-exec-run.yml` + `pipx install` is niet-standaard en wordt
  vervangen door deze variant. Rule matched op `$OBJ.exec_run($$$)` in files
  onder `klai-portal/backend/app/`. Allow-list via `ignores:` op
  `klai-portal/backend/tests/**`.

  Minimale `sgconfig.yml`:
  ```yaml
  ruleDirs:
    - rules
  ```

  `rules/no-exec-run.yml` (zie definitieve vorm in "Technische aanpak"):
  `severity: error` → ast-grep action exit-code ≠ 0 → CI rood.

- **M3.2**: integreer in `.github/workflows/portal-api.yml` — nieuwe step
  tussen `ruff check` en `pytest`:
  ```yaml
  - name: Guard — no exec_run in production code (SPEC-SEC-024)
    uses: ast-grep/action@cf62e780f0c88301228978d593a7784427a097a6  # v1.5.0 (SHA-pin)
    with:
      config: sgconfig.yml
      paths: klai-portal/backend/app
  ```
  **Alternatief overwogen**: semgrep is al in CI (`.github/workflows/semgrep.yml`).
  We kunnen deze guard ook als semgrep-regel onder `.semgrep/no-exec-run.yml`
  toevoegen en de bestaande semgrep workflow laat hem automatisch meenemen.
  Besluit: **ast-grep als primair** (de user-vraag luidde expliciet ast-grep,
  én de regel is AST-eenvoudig en heeft geen OWASP-classificatie nodig).
  Als de team-DX liever bij één tool blijft: val terug op semgrep.
- **M3.3**: test de guard lokaal — commit bewust een `exec_run` in een
  throwaway file, verifieer dat CI rood wordt, revert.

**Deliverables M3**: de rule-file, de workflow-step, een screenshot/log van
de bewuste rode run ter bewijs.

### M4 — Smoke-test + permanente alert + documentatie (priority: MEDIUM)

Doelen R9, R10, R11, R12, R13, R14.

- **M4.1** (smoke-test): schrijf `scripts/smoke-docker-socket-proxy.sh` (≤ 1 pagina):
  - voor elke `keep`-verb één probe-call via `curl` of `docker` CLI
  - `set -euo pipefail`, duidelijke output per verb (`[OK]` / `[FAIL]`)
  - Draait vanaf core-01 (heeft toegang tot `docker-socket-proxy:2375` via
    `socket-proxy` netwerk — we laten 'm binnen portal-api container draaien
    met `docker compose exec portal-api ...`).
  - **Drievoudig vangnet tegen dangling wegwerp-containers**:
    (1) `trap "docker rm -f $NOOP >/dev/null 2>&1 || true" EXIT` bovenaan —
    vuurt bij elke script-exit-route, inclusief crashes en Ctrl+C.
    (2) De container draait `busybox:musl sleep 60` — sterft sowieso na 60s
    zelfs als het trap-mechanisme faalt.
    (3) `docker run --rm` — Docker ruimt 'm automatisch op bij exit.
  - Sanity-check aan het einde: `docker ps --filter name=smoke-sec-024` moet
    leeg zijn; zo niet, exit ≠ 0.
- **M4.2** (permanente alert): maak een Grafana alert-rule en dashboard.
  - **Alert-rule** (LogQL via VictoriaLogs datasource):
    ```logql
    sum by (service) (count_over_time(
      {service=~"portal-api|runtime-api"}
      |= "Forbidden" |= "docker-socket-proxy" [5m]
    )) > 0
    ```
    Evaluation interval `1m`, `for: 0` (zero-tolerance — één hit = fire),
    severity `warning`, grouping `[alertname, service]`,
    `repeat_interval: 24h`, auto-resolve na 30m stil.
  - **Contact point**: hergebruik het bestaande Grafana e-mail contact
    point voor dev-alerts (geen Slack, geen PagerDuty in pre-launch).
  - **Dashboard**: één Grafana dashboard `Security — Proxy Denials` met:
    (a) time-series "403's per minuut per service" met deploy-annotaties
    (via Grafana annotation-datasource op GitHub Actions deploy-events),
    (b) tabel "laatste 20 violations" met `request_id` voor drill-through,
    (c) log-panel met raw lines.
  - **Provisioning**: alert en dashboard als Grafana provisioning YAML in
    `deploy/grafana/provisioning/` zodat ze ge-versioned en reproducibel
    zijn.
- **M4.3** (CI-hook smoke-test): haak de smoke-test vast achter
  `deploy-compose.yml` (of `portal-api.yml`) als non-blocking post-deploy
  step, initieel. Zodra hij 2 weken groen staat: blocking maken.
- **M4.4** (documentatie): update `.claude/rules/klai/platform/docker-socket-proxy.md`:
  nieuwe subsectie "Allowed verbs (per-verb rationale)" die de eind-tabel
  uit M2 samenvat, plus een verwijzing naar SPEC-SEC-024, het smoke-test
  script, én de permanente alert-rule.
- **M4.5** (end-to-end alert-validatie): bewijs dat de alert werkt door
  bewust in een staging-branch een `exec_run` te triggeren, te mergen,
  te deployen, en te asserteren dat binnen 2 minuten een e-mail binnenkomt.
  Revert daarna de regressie.

**Deliverables M4**: smoke-test script, Grafana provisioning YAML
(alert-rule + dashboard), CI-hook, rule-file update, screenshot/log van de
end-to-end alert-dry-run.

---

## Audit-output (M1 afgerond — 2026-04-21)

**Scan-queries die gedraaid zijn:**

```bash
# 1. Tekst-scan — vangt ook comments en docstrings
grep -rn "exec_run(" klai-portal/backend/

# 2. Non-whitelist Docker API calls in app/
grep -rn "\.top(\|\.attach(\|\.commit(\|\.export(\|\.diff(\|\.stats(\|images\.build\|images\.pull\|volumes\." klai-portal/backend/app/

# 3. Dynamic dispatch (R-3 mitigatie)
grep -rn "getattr.*exec\|/exec/\|/containers/.*/exec" klai-portal/backend/

# 4. Bredere Docker-client usage in app/ (voor verb rationale)
grep -rn "containers\.(get\|run\|list\|create)\|networks\.(get\|list\|create\|connect)\|client\.from_env" klai-portal/backend/app/

# 5. Overige callsites op dezelfde containers-objecten in infrastructure.py
grep -n "\.remove(\|\.restart(\|\.run(\|\.connect(\|\.disconnect(" klai-portal/backend/app/services/provisioning/infrastructure.py
```

> `ast-grep run -p '$OBJ.exec_run($$$)' --lang py klai-portal/backend/` niet lokaal
> uitgevoerd (CLI niet geïnstalleerd op dev-machine); de CI-step uit M3 dekt dit
> mechanisch vanaf merge. Tekst-grep (query 1) is in de tussentijd strikter — die
> vangt óók docstring-mentions. AST-grep zou _minder_ hits produceren dan grep; als
> grep schoon is op `app/`, is ast-grep het per definitie ook.

### Tabel A — `exec_run` callsites

| File:regel | Symbol | Verdict | Actie | Rationale |
|---|---|---|---|---|
| `klai-portal/backend/app/services/provisioning/infrastructure.py:10-11` | module docstring | acceptable | none | Rule-reference in docstring ("never through `container.exec_run([...])`"). Géén call — AST-scan matcht dit niet. |
| `klai-portal/backend/tests/services/provisioning/test_infrastructure.py:151,255,329,344,345` | `test_never_calls_docker_exec`, `test_never_calls_container_exec_run` + comments | acceptable | add-to-allowlist | Regressie-guards; ze _moeten_ `exec_run` noemen en mocken om te asserteren dat productiecode het niet aanroept. |
| `klai-portal/backend/tests/test_librechat_regenerate.py:7` | module docstring | acceptable | add-to-allowlist | Rule-reference comment in regressie-test. |
| `klai-portal/backend/scripts/**` | — | n.v.t. | none | Scan leverde 0 hits op. |
| `klai-portal/backend/app/**` (productie) | — | **clean** | none | **0 echte `exec_run` calls.** Historische hits (`infrastructure.py` Redis FLUSHALL, MongoDB user mgmt, chat-health probe) zijn gefixt in `c5653159`, `a3920a75`, `9dc03c67`. |

### Tabel B — Docker API calls buiten de whitelist in `klai-portal/backend/app/`

| File:regel | Call | Geraakte endpoint | Verdict |
|---|---|---|---|
| `klai-portal/backend/app/api/internal.py:991` | tekst-match op `/exec/*/start` in comment | n.v.t. (comment) | acceptable |
| — | `.top()`, `.attach()`, `.export()`, `.diff()`, `.stats(stream=True)` | — | **0 hits** |
| — | `images.build()`, `images.pull()` | — | **0 hits** |
| — | `volumes.create()`, `volumes.get()`, `volumes.list()`, `volumes.prune()` | — | **0 hits** |
| — | `networks.create()`, `networks.prune()` | — | **0 hits** (alleen `networks.get()` + `net.connect()` — zie Tabel C) |
| — | dynamic dispatch (`getattr(*, "exec*")`, `/containers/*/exec`) | — | **0 hits** in `app/` |

Alle `.commit()` en `.run()` hits die initieel als "verdachte" hits verschenen bleken SQLAlchemy `db.commit()` / `session.commit()` en niet-Docker `run()` methoden — uitgefilterd.

### Tabel C — Bewezen Docker-API gebruik in `app/` (input voor M2 verb rationale)

| Callsite | Docker endpoint | Verb gebruikt |
|---|---|---|
| `infrastructure.py:68` `client.containers.get(name)` | `GET /containers/{id}/json` | CONTAINERS |
| `infrastructure.py:69` `c.remove(force=True)` | `DELETE /containers/{id}?force=true` | CONTAINERS + DELETE |
| `infrastructure.py:135` `docker_client.containers.get(name)` | `GET /containers/{id}/json` | CONTAINERS |
| `infrastructure.py:136` `container.restart(timeout=10)` | `POST /containers/{id}/restart` | CONTAINERS + POST |
| `infrastructure.py:207` `client.containers.get(settings.caddy_container_name)` | `GET /containers/{id}/json` | CONTAINERS |
| `infrastructure.py:208` `caddy.restart(timeout=10)` | `POST /containers/{id}/restart` | CONTAINERS + POST |
| `infrastructure.py:222` `client.containers.get(container_name)` | `GET /containers/{id}/json` | CONTAINERS |
| `infrastructure.py:223` `old.remove(force=True)` | `DELETE /containers/{id}?force=true` | CONTAINERS + DELETE |
| `infrastructure.py:236` `client.containers.run(image=..., ...)` | `POST /containers/create` + `POST /containers/{id}/start` | CONTAINERS + POST |
| `infrastructure.py:254` `client.networks.get(net_name)` | `GET /networks/{id}` | NETWORKS |
| `infrastructure.py:255` `net.connect(container_name)` | `POST /networks/{id}/connect` | NETWORKS + POST |
| `api/internal.py:1020` `client.containers.get(container_name)` | `GET /containers/{id}/json` | CONTAINERS |

### Tabel D — Andere services met Docker socket access (buiten SEC-024 scope)

| Service | Compose-regel | Mount / host | Scope? | Reden |
|---|---|---|---|---|
| `alloy` | `compose:492` | `/var/run/docker.sock:/var/run/docker.sock:ro` (direct, read-only) | **out** | Observability sidecar, read-only, niet via proxy. Route via proxy zou log-collectie breken (Alloy moet _alle_ containers kunnen enumereren). |
| `cadvisor` | `compose:477` | `/var/run:/var/run:ro` (direct, read-only) | **out** | Container-metrics collector, read-only. Zelfde argument als Alloy. |
| `klai-scribe-*` | — | geen socket mount, geen docker client | **out** | Geen Docker API gebruik. |

Conclusie: SEC-024 scope blijft exact **portal-api + runtime-api** — de twee services die SEC-021 bewust achter de proxy heeft gezet. Monitoring-sidecars zijn out of scope; hun socket-access is read-only en een architecturale keuze. Als we die ook willen hardenen is dat een losse SPEC (bijv. een aparte socket-proxy instantie met alleen `CONTAINERS=1 INFO=1 EVENTS=1` voor monitoring).

### M1-conclusie

- Productie-code (`klai-portal/backend/app/**`) bevat **0 `exec_run` calls** en **0 non-whitelist Docker API calls**.
- De enige API's die portal-api feitelijk gebruikt: `containers.get/run/remove/restart` en `networks.get/connect`.
- Dit ondersteunt de agressieve reductie in M2 volledig: `CONTAINERS + NETWORKS + POST + DELETE` is minimaal en compleet voor portal-api. Alles anders mag weg.
- runtime-api blijft black-box; de permanente alert (M4.2) dekt onverwachte 403's daar.

---

## Verb rationale (in te vullen in M2, preview-tabel)

Op basis van de huidige code (na vandaag's fixes):

| Verb env-var | Status vandaag | Productie-code die de verb nodig heeft | Voorstel |
|---|---|---|---|
| `CONTAINERS` | 1 | `containers.get(name).restart()` (`infrastructure.py:135,207`), `containers.get(name).remove(force=True)` (`infrastructure.py:68,223`), `containers.run(...)` (`infrastructure.py:236`), `containers.get()` (diverse) | **keep** — kern van tenant-provisioning |
| `NETWORKS` | 1 | `client.networks.get(net_name).connect(container_name)` (`infrastructure.py:254` — LibreChat sidecars aansluiten op `klai-net-mongodb` etc.) | **keep** — noodzakelijk voor LibreChat multi-network setup |
| `POST` | 1 | `restart()`, `connect()`, `run()` — allemaal POST varianten op `CONTAINERS`/`NETWORKS` | **keep** — schakelaar voor de bovenstaande |
| `DELETE` | 1 | `container.remove(force=True)` — provisioning rollback + stale-container cleanup | **keep** — zonder dit blijft een gefaalde provisioning hangen |
| `EXEC` | 0 (niet gezet) | — geen productie-pad | **keep not-set** (expliciet nooit zetten — zie SPEC) |
| `IMAGES` | 0 | `containers.run(image=...)` *kan* een pull triggeren als image ontbreekt | **agressief drop (default)**: we pinnen alle images en pullen tijdens deploy. Als runtime-api stilletjes `images.pull` doet bij bot-spawn, vangt de alert dat — dan zetten we 'm terug. |
| `VOLUMES` | 0 | — | **keep not-set** |
| `BUILD` | 0 | — | **keep not-set** |
| `SYSTEM` | 0 | — | **keep not-set** |
| `PLUGINS` | 0 | — | **keep not-set** |

Conclusie preview (pre-launch, agressieve strategie): start met precies
`CONTAINERS+NETWORKS+POST+DELETE`, niets extra. Elke andere verb blijft
niet-gezet. Als de alert vuurt na de eerste runtime-api-bot-spawn, voegen
we gericht die ene verb toe — niet preventief.

---

## Technische aanpak per deliverable

### ast-grep project layout (M3.1)

Industry-standaard indeling voor `ast-grep/action@latest`:

```
sgconfig.yml              # repo root — tells ast-grep where to find rules
rules/
  no-exec-run.yml         # the actual guard
```

**`sgconfig.yml`** (repo root):

```yaml
ruleDirs:
  - rules
```

**`rules/no-exec-run.yml`**:

```yaml
id: no-exec-run-in-production
language: python
severity: error
message: >
  exec_run() is forbidden in production code (SPEC-SEC-024).
  docker-socket-proxy blocks POST /exec/*/start by design.
  Use the service's native wire protocol instead — see
  .claude/rules/klai/platform/docker-socket-proxy.md.
rule:
  pattern: $OBJ.exec_run($$$)
files:
  - klai-portal/backend/app/**/*.py
# Explicit allow-listing for regression guards — MUST stay.
ignores:
  - klai-portal/backend/tests/**/*.py
```

### Smoke-test script (M4.1) — schets

```bash
#!/usr/bin/env bash
# scripts/smoke-docker-socket-proxy.sh
# Proof that every "keep"-verb on docker-socket-proxy still works after deploy.
# SPEC-SEC-024-R10. Run from core-01 (or inside portal-api container).
set -euo pipefail

PROXY="http://docker-socket-proxy:2375"
NOOP="smoke-sec-024-$(date +%s)"

trap 'docker rm -f "$NOOP" >/dev/null 2>&1 || true' EXIT

echo "[*] CONTAINERS (GET /containers/json)"
curl -sf "$PROXY/v1.47/containers/json?limit=1" > /dev/null && echo "[OK]"

echo "[*] NETWORKS (GET /networks)"
curl -sf "$PROXY/v1.47/networks" > /dev/null && echo "[OK]"

echo "[*] POST (start a throwaway busybox)"
docker run --rm -d --name "$NOOP" --network none busybox:musl sleep 60 > /dev/null
curl -sf -X POST "$PROXY/v1.47/containers/$NOOP/restart?t=1" && echo "[OK]"

echo "[*] DELETE (remove the throwaway)"
curl -sf -X DELETE "$PROXY/v1.47/containers/$NOOP?force=true" && echo "[OK]"

echo "[*] EXEC (must FAIL — proxy must block)"
if curl -sf -X POST "$PROXY/v1.47/containers/$NOOP/exec" \
     -H 'Content-Type: application/json' \
     -d '{"Cmd":["true"]}' > /dev/null 2>&1; then
  echo "[FAIL] EXEC is reachable — this is a regression!"
  exit 1
else
  echo "[OK] EXEC correctly blocked"
fi
```

---

## Integratie met bestaande workflows

- **Deploy**: standaard `deploy-compose.yml` flow. Compose-changes in
  `deploy/docker-compose.yml` syncen automatisch naar core-01.
- **CI**: `.github/workflows/portal-api.yml` — nieuwe ast-grep step (M3.2).
- **Observability**: post-deploy check via VictoriaLogs MCP
  (`service:portal-api AND "Forbidden"`) — R12.
- **Docs**: `.claude/rules/klai/platform/docker-socket-proxy.md` krijgt
  de "Allowed verbs" subsectie (M4.3).

---

## Risico's (wat kan er misgaan tijdens implementatie)

### R-1 — We droppen een verb die stil nodig was (HIGH)
Scenario: audit mist een pad dat alleen in zeldzame flow (bijv. admin-API,
disaster recovery, alembic migration via docker) de verb nodig heeft. Na
deploy faalt die flow pas wanneer iemand hem triggert — mogelijk dagen later.

**Mitigatie**:
- M1 scanned *ook* `scripts/`, `migrations/` en docs-runbooks, niet alleen
  `app/`.
- M4.1 smoke-test draait post-deploy, maar dekt alleen verwachte verbs —
  een "drop"-verb die stil nodig was wordt pas bij de echte aanroep zichtbaar.
- Aanvullend: 24h VictoriaLogs-monitoring (R12). 403 met
  `docker-socket-proxy` in de chain is een directe rollback-trigger.
- **Rollback-procedure**: revert de compose-commit (één-commit revert, zoals
  bij SEC-021). Redeploy. Herstel < 5 min.

### R-2 — runtime-api breken (LOW in pre-launch)
runtime-api is een black-box vendored image. We weten niet welke Docker
endpoints hij gebruikt. Een verb-reductie die voor portal-api veilig is,
kan runtime-api op onverwachte plekken breken (bot-spawn kan crashen).

**Risk-downgrade**: pre-launch is een gebroken bot-spawn geen incident, het
is een leer-signaal. De permanente alert (M4.2) maakt de breuk binnen
minuten zichtbaar; één compose-edit zet de verb terug.

**Mitigatie**:
- Permanente alert met e-mail-notificatie (R12/R13) vangt runtime-api 403's.
- Bot-spawn smoke: bij de eerste scheduled meeting post-deploy, controleer
  handmatig dat de bot verschijnt. Als niet: check mail, zet verb terug.
- Geen preventief verb-handhaven "voor de zekerheid". Dat ondermijnt het
  doel van deze SPEC.

### R-3 — False negatives in de scan (MEDIUM)
`exec_run` kan aangeroepen worden via dynamische attribuutaccess
(`getattr(ctr, "exec_" + suffix)(...)`) of via een wrapper. grep + ast-grep
vangen dat niet.

**Mitigatie**:
- M1 bevat ook een tweede scan: `grep -rn "getattr.*exec\|/exec/\|/containers/.*/exec"`.
- Tests-as-oracle: `tests/services/provisioning/test_infrastructure.py`
  bevat al `assert not container.exec_run.called` — elke reintroductie via
  een wrapper rond `exec_run` laat die assertie falen.

### R-4 — CI-guard breekt een legitieme test-pattern (LOW)
ast-grep rule match per ongeluk iets in tests dat geen `exec_run` call is
maar wel zo leest.

**Mitigatie**:
- Allow-list voor `tests/` is expliciet (M3.1).
- Vóór merge: lokale dry-run `ast-grep scan ...` op de volledige repo. Elke
  onverwachte match wordt gelabeld of de pattern aangescherpt.

### R-5 — Smoke-test zelf wordt een attack-surface (LOW)
Een script dat "probe" calls doet tegen de proxy kan, als het slecht
geschreven is, onbedoeld iets aanpassen in productie (bijv. een bestaande
container restarten). Zie `_start_librechat_container` — productie-containers
hebben `restart_policy: unless-stopped`, `restart()` op een tenant is
disruptief.

**Mitigatie**:
- Smoke-test maakt uitsluitend een **wegwerp-container** (`busybox:musl`,
  `--rm`, een willekeurige unieke naam `smoke-sec-024-$(date +%s)`) voor de
  POST/DELETE probes. Geen enkele probe raakt een bestaande productie-container.
- `trap ... EXIT` garandeert opruiming zelfs bij script-abort.

---

## Verificatie-checklist (pre-merge)

- [ ] M1 audit-tabellen volledig ingevuld (geen "TBD" rijen)
- [ ] M2 `deploy/docker-compose.yml` diff alleen in `environment:` keys van
      `docker-socket-proxy`, **agressief gereduceerd**
- [ ] M3 ast-grep rule file aanwezig, CI-step aanwezig, bewezen
      rood-→-groen cyclus
- [ ] M4.1 smoke-test script bestaat, draait lokaal tegen staging-compose,
      blocks EXEC correct, ruimt wegwerp-containers op (drievoudig vangnet)
- [ ] M4.2 Grafana alert-rule + dashboard als provisioning YAML gecommit
- [ ] M4.5 end-to-end alert-dry-run: bewust `exec_run` op staging →
      e-mail-alert binnen 2 minuten ontvangen → regressie gereverted
- [ ] Rule `docker-socket-proxy.md` update gecommit
- [ ] SEC-021 spec gelinkt vanuit dit plan + vice versa (cross-ref)

---

## Deploy + rollback

**Deploy**: push branch → PR → CI groen → merge → `deploy-compose.yml`
syncen automatisch → `docker compose up -d docker-socket-proxy portal-api
runtime-api` op core-01.

**Verify**:
```bash
ssh core-01 "docker compose exec docker-socket-proxy env | grep =1"
ssh core-01 "/opt/klai/scripts/smoke-docker-socket-proxy.sh"
```

**Rollback**: **fix-forward** in pre-launch. Als iets breekt, push je een
nieuwe compose-commit die de verb terugzet (of de regressie anderszins
oplost). Geen afzonderlijke rollback-procedure, geen revert-ceremonie.
De permanente alert (M4.2) vertelt je direct welke verb terug moet.

**Permanente monitoring**: Grafana alert-rule + dashboard
(`Security — Proxy Denials`). E-mail vuurt bij elke 403 op
`docker-socket-proxy` voor portal-api of runtime-api. Zie R12–R14.
