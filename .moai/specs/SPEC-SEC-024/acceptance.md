# SPEC-SEC-024 — Acceptance Criteria

Alle scenario's zijn Given/When/Then en direct testbaar. Elk scenario mapt naar
één of meer `SPEC-SEC-024-Rn` requirements uit `spec.md`.

---

## AC-1 — Volledige `exec_run` scan levert bewijs op

**Ref**: R1, R3

**Given** de repo op commit-SHA van de merge-ready PR
**And** de aanwezige fixes van 2026-04-21 (`c5653159`, `a3920a75`, `9dc03c67`) in main
**When** we uitvoeren:
```bash
grep -rn "exec_run(" klai-portal/backend/
ast-grep run -p 'exec_run($$$)' --lang py klai-portal/backend/
```
**Then** alle matches vallen ofwel onder `klai-portal/backend/tests/**`
(regressie-guards, expliciet toegestaan), ofwel zijn gedocumenteerd in
`plan.md` sectie "Audit-output" met verdict `acceptable` + rationale.
**And** er is GEEN match onder `klai-portal/backend/app/**`.

---

## AC-2 — Non-whitelist Docker API calls zijn geïnventariseerd

**Ref**: R2

**Given** de audit is uitgevoerd in M1
**When** we de scan-query draaien:
```bash
grep -rn "\.top(\|\.attach(\|\.commit(\|\.export(\|\.diff(\|images\.build\|images\.pull\|volumes\." klai-portal/backend/app/
```
**Then** elke hit staat in de "Docker API calls buiten de whitelist" tabel
in `plan.md` met verdict en bron-regel.
**And** er zijn GEEN hits zonder verdict.

---

## AC-3 — Proxy-whitelist is per-verb gerechtvaardigd

**Ref**: R4, R5

**Given** de audit-tabellen in `plan.md` zijn volledig
**When** we `deploy/docker-compose.yml` inspecteren (de `docker-socket-proxy`
`environment:` block)
**Then** elke `=1` variabele heeft in `plan.md` sectie "Verb rationale" een
rij met minimaal één concreet code-pad dat die verb gebruikt (file:regel
referentie).
**And** elke variabele die *niet* gezet is heeft status `keep not-set`
met explicit rationale, óf staat niet in de tabel (omdat hij irrelevant
is voor de scope).

---

## AC-4 — Forbidden verbs zijn en blijven uit

**Ref**: R6

**Given** de gedeploye compose op core-01
**When** we uitvoeren:
```bash
ssh core-01 "docker compose exec docker-socket-proxy env"
```
**Then** de output bevat GEEN van: `EXEC=1`, `BUILD=1`, `VOLUMES=1`,
`SYSTEM=1`, `PLUGINS=1`.
**And** als iemand een PR opent die één van deze zet, faalt de CI
(via M3 ast-grep check op compose OF via een semgrep-regel op
`deploy/docker-compose.yml`).

---

## AC-5 — CI-guard blokkeert een nieuwe `exec_run` in productie-code

**Ref**: R7, R8

**Given** de branch `sec-024-test-regression` met één bewuste regressie:
```python
# klai-portal/backend/app/services/provisioning/infrastructure.py
container = docker_client.containers.get("x")
container.exec_run(["echo", "test"])  # intentioneel regressief
```
**When** de CI draait in een pull request
**Then** de step `Guard — no exec_run in production code` faalt met exit-code ≠ 0
**And** de GitHub Actions output bevat de regel:
`exec_run() is forbidden in production code (SPEC-SEC-024)`.
**And** de merge-knop is disabled (required-status-check).

**Teruggekeerd gedrag**:
**When** we de regressie-regel weghalen en opnieuw pushen
**Then** dezelfde step slaagt.

---

## AC-6 — Allow-list voor tests werkt

**Ref**: R7 (allow-list clause)

**Given** de bestaande tests in `klai-portal/backend/tests/services/provisioning/test_infrastructure.py`
met hun `assert not container.exec_run.called` regressie-guards
**When** de CI `ast-grep scan` step draait
**Then** deze bestaande `exec_run` mentions in tests triggeren GEEN failure.
**And** het `ignores:` block in `.ast-grep/no-exec-run.yml` verwijst expliciet
naar `klai-portal/backend/tests/**`.

---

## AC-7 — Smoke-test valideert elke keep-verb post-deploy

**Ref**: R10, R11

**Given** de nieuwe compose is gedeploy op core-01
**When** we uitvoeren:
```bash
ssh core-01 "/opt/klai/scripts/smoke-docker-socket-proxy.sh"
```
**Then** het script exiteert met code 0.
**And** stdout bevat één `[OK]` per keep-verb (`CONTAINERS`, `NETWORKS`,
`POST`, `DELETE` minimaal).
**And** stdout bevat `[OK] EXEC correctly blocked` — `POST /exec/*/start`
moet nog steeds 403 teruggeven.
**And** GEEN wegwerp-container `smoke-sec-024-*` blijft draaien na afloop
(verifieer met `docker ps --filter name=smoke-sec-024`).

**Faal-scenario**:
**Given** iemand drop `POST=1` per ongeluk uit de whitelist
**When** de smoke-test draait post-deploy
**Then** de "POST" probe geeft een 403 terug, het script exiteert met code ≠ 0,
en de deploy-workflow markeert de deploy als failed.

---

## AC-8 — Documentatie is gesynct

**Ref**: R9

**Given** de merge is afgerond
**When** we openen `.claude/rules/klai/platform/docker-socket-proxy.md`
**Then** het bestand bevat een subsectie `## Allowed verbs (per-verb rationale)`.
**And** elke verb in die subsectie komt letterlijk terug uit de
"Verb rationale" tabel in `plan.md` (same values, same rationale).
**And** de subsectie verwijst naar `SPEC-SEC-024` als bron.

---

## AC-9 — Permanente alert-rule en dashboard bestaan en werken

**Ref**: R12, R13, R14

**Given** de SPEC-implementatie is merged
**When** we `deploy/grafana/provisioning/` inspecteren
**Then** er bestaat een alert-rule YAML met:
- query die matcht op `{service=~"portal-api|runtime-api"} |= "Forbidden" |= "docker-socket-proxy"` over een `[5m]` venster,
- threshold `> 0`, evaluation interval `1m`, `for: 0`,
- grouping `[alertname, service]`, `repeat_interval: 24h`,
- auto-resolve-duration `30m`,
- contact point = bestaand dev-alerts e-mail-contact, severity `warning`.

**And** er bestaat een dashboard YAML `Security — Proxy Denials` met
minimaal: (a) time-series 403's-per-service, (b) tabel recente violations
met request_id, (c) deploy-annotaties.

**End-to-end bewijs** (R14):
**Given** een staging-branch `sec-024-alert-dryrun` met één bewuste
`exec_run(...)` in productiecode, gemerged en gedeployd naar staging
**When** de eerste call op dat pad een 403 produceert
**Then** binnen 2 minuten ontvangt het dev-alerts e-mailadres een
alert-notificatie met service-label en request_id.
**And** na revert + 30 minuten stilte auto-resolved de alert zonder
handmatige actie.

---

## Definition of Done

Alle onderstaande punten zijn AND-gekoppeld:

- [ ] **AC-1** groen — scan bewijst geen `exec_run` in `app/`
- [ ] **AC-2** groen — non-whitelist API calls geïnventariseerd
- [ ] **AC-3** groen — elke keep-verb heeft een code-pad rationale
- [ ] **AC-4** groen — forbidden verbs zijn niet gezet
- [ ] **AC-5** groen — CI-guard vangt regressie
- [ ] **AC-6** groen — test-allow-list werkt
- [ ] **AC-7** groen — smoke-test passeert post-deploy + containers opgeruimd
- [ ] **AC-8** groen — pitfall-rule bevat "Allowed verbs" sectie
- [ ] **AC-9** groen — permanente alert-rule + dashboard + e-mail werken end-to-end

**Schrapt vervangen door fix-forward-strategie**: geen aparte rollback-AC.
Als een reductie iets breekt, push een nieuwe compose-commit die de verb
terugzet. De alert (AC-9) wijst aan welke verb.

**Quality gates** (TRUST 5, per `moai-constitution.md`):

- **T** — tested: smoke-test script heeft een minimale unit-test (of
  dry-run in CI-staging) die zijn exit-codes verifieert tegen een mock-proxy
- **R** — readable: elke PR bevat rationale in commit-body, SPEC-SEC-024
  in subject-line
- **U** — unified: alleen `docker-socket-proxy.environment` gewijzigd in
  compose (geen onverwante diffs)
- **S** — secured: audit rapporteert 0 nieuwe HIGH+ findings in semgrep
  voor de gewijzigde files
- **T** — trackable: commit subject-pattern
  `chore(sec): SPEC-SEC-024 Mx.y ...` per milestone

---

## Test-scenario's — Given/When/Then (samenvatting)

1. **CI-regressie blokkade** — AC-5 (normal path)
2. **Test-allow-list happy path** — AC-6 (normal path)
3. **Smoke-test alle keep-verbs** — AC-7 (normal path)
4. **Smoke-test detecteert gebroken reductie** — AC-7 (error path)
5. **EXEC blijft 403** — AC-7 (security-critical path)
6. **Smoke-test ruimt wegwerp-container op** — AC-7 (hygiene, drievoudig vangnet)
7. **End-to-end alert-dry-run triggert e-mail** — AC-9 (observability, pre-merge)

---

## Edge cases

- **Vendored runtime-api doet iets onverwachts**: AC-9 runtime-api check
  dekt dit. Als runtime-api 403's genereert, rollback via AC-10.
- **`images.build`/`pull` blijkt tóch gebruikt**: AC-2 vangt dit tijdens
  audit; `plan.md` "Verb rationale" zou dan `IMAGES` op `keep` zetten
  voor de deploy.
- **Iemand hernoemt `exec_run` naar een wrapper**: ast-grep pattern is
  `$OBJ.exec_run($$$)` — directe match. Voor `getattr(obj, "exec_run")(...)`
  moet de pattern aangescherpt worden — opgenomen als M1.3 scan-query.
- **Toekomstige migratie-script gebruikt `exec_run`**: CI-guard werkt op
  `app/` only (niet op `scripts/migrations/`), maar M4.3 documenteert:
  migrations horen óók niet via exec te lopen — ze gebruiken alembic via
  het portal-api proces zelf.

---

## Referenties

- `.moai/specs/SPEC-SEC-024/spec.md` — EARS requirements
- `.moai/specs/SPEC-SEC-024/plan.md` — implementatie-plan + audit-output
- `.moai/specs/SPEC-SEC-021/spec.md` — parent SPEC (proxy-routing)
- `.claude/rules/klai/platform/docker-socket-proxy.md` — pitfall-regel
- Observability MCP: VictoriaLogs via `.mcp.json` `victorialogs` server
