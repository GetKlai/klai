# Klai Test-Suite Plan

> Standaard test-suite voor klai. Combineert bestaande unit/integratie tests
> met een nieuwe production-tenant e2e die de hele frontend dekt na
> deploy. Uitbreidbaar per-journey.

Status: **DRAFT** — wordt stap voor stap uitgewerkt. Zie `## 11. Rollout` voor de fasering.

---

## 1. Doel

1. Eén canonieke "klai test suite" die elke deploy mechanisch verifieert.
2. Combinatie van snelle unit-tests + langzamere e2e met echte UI-flow.
3. **Uitbreidbaar:** een nieuwe e2e-journey toevoegen = één nieuwe file in
   een vaste structuur, geen runner-aanpassing.
4. **Reproducibel:** geen handmatige seed-stappen per run; vaste e2e-tenant
   met bekende credentials + TOTP secret in CI.
5. **Self-cleaning:** elke e2e-run laat de tenant in een schone staat
   achter (vereist SPEC-INFRA-TENANT-DELETE-001).

---

## 2. Test-pyramid voor klai

| Laag | Tool | Locatie | Runtime | Wanneer | Wat |
|---|---|---|---|---|---|
| **Unit** | `vitest` | `klai-portal/frontend/**/*.test.ts` | <30s | elke commit | Pure component-/util-tests |
| **Unit** | `pytest` | `klai-portal/backend/tests/unit/` | <60s | elke commit | Pure functie-tests |
| **Integration** | `pytest` | `klai-portal/backend/tests/services/` | <3 min | elke commit | DB + Redis + mocks van Zitadel/LiteLLM |
| **CI quality gates** | `ruff`, `pyright`, `eslint`, `pip-audit`, `npm audit`, `Trivy`, `Semgrep` | inline | <5 min | elke commit | Lint + type + security |
| **Compose audit** | `audit-compose-orphans.sh`, `audit-compose-volumes.sh` | `scripts/` | <30s | wanneer compose/Caddyfile wijzigt | SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2c |
| **E2E dev-stack** | Playwright | `klai-portal/frontend/e2e/*.spec.ts` | ~2 min | manueel + nightly | KB-quota, templates (bestaande specs, dev-stack `localhost:5173`) |
| **E2E prod-tenant** ⭐ | Playwright | `klai-portal/frontend/e2e/prod-tenant/*.spec.ts` | ~5 min | na elke deploy + nightly | Hele frontend tegen `e2e.getklai.com` |
| **Smoke (post-deploy)** | bash | `scripts/smoke-*.sh` | <30s/elk | direct na elke service-deploy | docker-socket-proxy, ssrf-isolation, persistence (bestaand) |

**Nieuw in dit plan:** alleen de "E2E prod-tenant" rij (⭐). Rest bestaat al.

---

## 3. Standaard suite-aanroep

Eén canonieke test-runner ladder voor lokaal én CI:

```bash
make test                  # unit + integration + lint (snelle gate, <5 min)
make test:e2e:dev          # alle dev-stack e2e (vereist lokale dev-stack)
make test:e2e:prod         # prod-tenant e2e (vereist E2E_TOTP_SECRET)
make test:all              # alles in volgorde — gebruik in nightly CI
```

In CI (`.github/workflows/`) blijft het per-service-workflow patroon:
- `portal-api.yml` → unit + integration (bestaat)
- `portal-frontend.yml` → vitest (bestaat)
- **Nieuw:** `e2e-prod-tenant.yml` → triggered op succesvolle `deploy-compose.yml`/`portal-api.yml`/`portal-frontend.yml`/`klai-knowledge-mcp.yml`/etc., draait Playwright tegen `e2e.getklai.com` (isolated-tenant mode)

### 3.1 Twee modes — hoe ze samenspelen

```
┌─────────────────────────┬─────────────────────────────────────────────┐
│ isolated-tenant         │ voys-attached                               │
├─────────────────────────┼─────────────────────────────────────────────┤
│ E2E_MODE=isolated-tenant│ E2E_MODE=voys-attached                      │
│ (default)               │                                             │
│                         │                                             │
│ Login: J01 spec doet    │ Login: éénmalig handmatige Google SSO       │
│ email + password + TOTP │ capture; daarna session-cookie reuse        │
│ via _lib/auth.ts        │                                             │
│                         │                                             │
│ Tenant: e2e.getklai.com │ Tenant: voys.getklai.com (echte data —      │
│ (dedicated bot tenant)  │ Voys is gewoon main, niet aparte tenant)    │
│                         │                                             │
│ Where: lokaal + CI      │ Where: lokaal + CI (storage-state als       │
│                         │ base64-secret E2E_VOYS_STORAGE_STATE_B64)   │
│                         │                                             │
│ Cleanup: alles dat e2e  │ Cleanup: ALLEEN artifacts met prefix        │
│ aanmaakt mag weg        │ `e2e-{run-ts}-`. Cleanup weigert mechanisch │
│                         │ items zonder die prefix te verwijderen.     │
└─────────────────────────┴─────────────────────────────────────────────┘
```

**Beide modes** draaien dezelfde journeys. Het enige verschil zit in
J01 (login) — die wordt overgeslagen in `voys-attached` omdat de browser-
sessie al ingelogd is via de capture-step. Naming convention met prefix
geldt overal — ook in isolated-tenant — om consistentie tussen modes te
borgen en parallelle runs niet te laten botsen.

**Bonus voor voys-attached:** de gecaptureerde Google sessie kan ook
buiten de Klai-portal — bijvoorbeeld een Google Meet aanmaken via
`meet.google.com/new`. Daardoor is de Vexa-meeting-flow autonoom
testbaar (zie J12).

---

## 4. Architecture: prod-tenant e2e

### 4.1 Bestandsstructuur (uitbreidbaar)

```
klai-portal/frontend/e2e/prod-tenant/
├── _lib/
│   ├── auth.ts             # login + TOTP helper (otplib)
│   ├── fixtures.ts         # test-fixtures (canary KB doc, audio, etc.)
│   ├── cleanup.ts          # KB delete, template delete, etc.
│   └── poll.ts             # generieke async-status polling
├── _config/
│   ├── playwright.prod.config.ts   # baseURL = E2E_BASE_URL
│   └── storageState.json   # geserialiseerde session na J1 (gitignored)
├── J01-login.spec.ts
├── J02-chat.spec.ts
├── J03-knowledge.spec.ts
├── J04-gaps.spec.ts
├── J05-templates.spec.ts
├── J06-scribe.spec.ts
├── J07-account.spec.ts
├── J08-admin-readonly.spec.ts
├── J09-admin-settings-write.spec.ts
├── J10-logout.spec.ts
├── J11-headers-perf.spec.ts
└── README.md               # hoe een nieuwe journey toevoegen
```

**Een nieuwe journey toevoegen** = één nieuwe `J##-naam.spec.ts` en
de `_lib/` herbruiken. Geen runner-aanpassing, geen workflow-aanpassing.

### 4.2 Auth helper

`_lib/auth.ts`:

```ts
import { authenticator } from 'otplib'
import type { Page } from '@playwright/test'

export async function loginAsE2EBot(page: Page) {
  await page.goto('/')
  await page.fill('[name=email]', process.env.E2E_USER_EMAIL!)
  await page.fill('[name=password]', process.env.E2E_USER_PASSWORD!)
  await page.click('button:has-text("Inloggen")')

  // TOTP step
  await page.waitForSelector('[name=totp]', { timeout: 5000 })
  const code = authenticator.generate(process.env.E2E_TOTP_SECRET!)
  await page.fill('[name=totp]', code)
  await page.click('button:has-text("Verifiëren")')

  await page.waitForURL(/\/app\//, { timeout: 15000 })
}

export async function persistAuthState(page: Page, path: string) {
  await page.context().storageState({ path })
}
```

### 4.3 Storage-state hergebruik

J01 logt in en serialiseert de browser-state naar
`_config/storageState.json`. J02..J11 starten elk met die state geladen,
geen herhaalde TOTP. Totale runtime ~5 min ipv ~15.

### 4.4 Failure-classificatie

Per journey in `expect`-asserties:

- **Hard fail:** assertie faalt, console bevat `TypeError`/`ReferenceError`,
  netwerk-call returnt 5xx → spec-failure
- **Soft fail (warn-only):** page-load >3s, niet-fatale 4xx (zoals `401` op
  `/api/auth/session` direct na navigatie) → console warning maar groen

---

## 5. Journeys

11 journeys. Focus is **niet** in dit plan — die feature is uitgefaseerd
(geverifieerd dat `/app/focus` bestaat als route maar de UI is verwijderd).
Als focus terugkomt: nieuwe `J##-focus.spec.ts`, dat is precies waar de
uitbreidbaarheid voor is.

| # | Journey | Routes | Wat verifieert het |
|---|---|---|---|
| **J01** | **Login + TOTP** | `/login` → TOTP step → `/app/*` | Auth-keten Zitadel + TOTP + portal-api sessie-cookie. Persisteert storage-state voor J02..J11 |
| **J02** | **Chat round-trip** | `/app/chat` | LibreChat embed laadt, LiteLLM model routing, streaming response. Type "antwoord met enkel het woord pong"; assert response bevat exact "pong". Verifieert: librechat-{slug} container, LiteLLM, model-API |
| **J03** | **Knowledge upload + RAG search** | `/app/knowledge` | Upload `e2e-fixture.md` (bevat unieke canary "klai-e2e-canary-string-42"), wacht op ingestion `ready` (poll), open `/app/chat`, vraag "Wat is de canary string in mijn KB?", assert antwoord bevat de exacte canary. Verifieert hele RAG-pipeline: knowledge-ingest, BGE-M3 (TEI), sparse-server, Qdrant, FalkorDB, retrieval-api, klai-knowledge-mcp, LiteLLM hook. Cleanup: KB delete |
| **J04** | **Knowledge gaps dashboard** | `/app/gaps` | Page rendert, filters werken, table heeft >=0 rows. Geen 5xx |
| **J05** | **Templates CRUD + activatie** | `/app/templates` (user) | Create template "E2E test {timestamp}", activate, ga naar `/app/chat`, send query, assert system-prompt actief (template-tag in response of via expliciete UI-indicator). Cleanup: template deactivate + delete |
| **J06** | **Scribe upload + transcribe** | `/app/scribe` of `/app/transcribe` | Upload `e2e-fixture.wav` (~3s "test test test"), poll voor `transcribed` status, assert text-output bevat "test". Cleanup: transcript delete |
| **J07** | **Account / locale switch** | `/app/account` | Profile-card rendert, switch NL→EN, herlaad, locale persisteert |
| **J08** | **Admin read-only routes** | `/admin/billing`, `/users`, `/groups`, `/api-keys`, `/mcps`, `/domains`, `/join-requests`, `/settings`, `/templates`, `/widgets` | Elk: 200, hoofdkop zichtbaar, table/form rendert, geen 5xx in network. Read-only — geen wijzigingen |
| **J09** | **Admin settings write** | `/admin/settings` | Wijzig display-name "Klai E2E {ts}", save, refresh, persisted. Cleanup: terug naar default |
| **J10** | **Logout flow** | Logout → `/logged-out` → terug-navigatie → redirect naar `/login` | Sessie weg, geen residual auth-cookie |
| **J11** | **Headers + performance** | Op elke route uit J01-J10 | Meet bij elk page-bezoek: HSTS aanwezig, CSP set, X-Frame-Options aanwezig, p99 page-load <3s. Resultaat als HTML-rapport, soft fail |
| **J12** | **Vexa meeting + transcript** (voys-attached only) | `meet.google.com/new` → `/app/meetings` → `/app/transcribe` | Met de gecaptureerde Google sessie navigeert de spec naar `meet.google.com/new`, leest de Meet-URL, opent een tweede tab op `/app/meetings`, plakt de URL, klikt "+ New meeting". Wacht max 60s tot de Vexa-bot join't (zichtbaar via deelnemerlijst-API). Spreekt 30s "test test test" via een audio-loopback (gegenereerd uit `e2e-fixture.wav` 10× herhaald) of laat browser TTS draaien. Stopt de meeting. Polling op `/api/scribe/transcripts` tot transcript-status = `transcribed`. Assert text bevat "test". Cleanup: transcript delete. Vereist VOYS-ATTACHED-mode (Google sessie nodig); skipt in isolated-tenant. |

**Bewust niet in scope** voor v1:
- `/app/docs` (klai-docs) — eigen e2e-spec voor docs-app, aparte runner
- `/app/transcribe` apart van `/app/scribe` — beide testen levert weinig
  extra coverage

---

### 5.1 Vexa meeting (J12, voys-attached only)

Eerder gedacht: hand-trigger door Mark. Bij nadere inspectie kan de
gecaptureerde Google sessie ook `meet.google.com/new` openen — dus J12
kan autonoom in voys-attached mode:

- **Mode:** voys-attached only. Skipt automatisch in isolated-tenant
  (geen Google sessie).
- **Cadens:** elke voys-attached run (lokaal of CI met
  E2E_VOYS_STORAGE_STATE_B64).
- **Audio-input:** loopback van `e2e-fixture.wav` of browser TTS via
  `speechSynthesis`. Spec-implementatie kiest wat het beste werkt op
  de Vexa-bot-pipeline.
- **Logging bij failure:** query in VictoriaLogs:
  `(service:vexa-meeting-api OR service:scribe-api OR service:vexa-bot)
  AND level:error AND _time:[meeting_start, meeting_start+10m]`.

Spec-skelet komt in fase 4 als `J12-meeting-vexa.spec.ts` — gemarkeerd
met `test.skip(E2E_MODE !== 'voys-attached', 'requires Google session')`.

## 6. Test-fixtures

Statische bestanden in `klai-portal/frontend/e2e/prod-tenant/fixtures/`:

| Fixture | Inhoud | Gebruikt door |
|---|---|---|
| `e2e-fixture.md` | ~10 regels, één unieke canary "klai-e2e-canary-string-42" | J03 |
| `e2e-fixture.wav` | ~3s mono 16kHz "test test test" | J06 |

**Generator script** (`fixtures/generate.sh`) zodat fixtures reproduceerbaar
zijn als ze ooit gewijzigd worden.

---

## 7. Prerequisites

Status geverifieerd 2026-05-15. #3 (TOTP) is intentioneel niet enrolled
op de e2e-bot; `auth.ts` probet de TOTP-form en skipt de stap als die
niet verschijnt, dus de MFA-off bot werkt zonder seed.

| # | Prerequisite | Wie | Status |
|---|---|---|---|
| 1 | E2E-tenant `e2e.getklai.com` op productie aangemaakt | jij (Mark) handmatig via signup | done |
| 2 | E2E-user `e2e@getklai.com` met password | jij (gegenereerd) | done |
| 3 | TOTP setup voor e2e-user | jij | skipped — niet enrolled; `auth.ts` skipt de stap |
| 4 | GitHub Secrets: `E2E_USER_EMAIL`, `E2E_USER_PASSWORD`, `E2E_BASE_URL` (+ optioneel `E2E_TOTP_SECRET` als #3 ooit wel gebeurt) | jij | done (`gh secret list`, gezet 2026-05-13) |
| 5 | SPEC-INFRA-TENANT-DELETE-001 gemerged (zodat J03/J05/J06/J09 cleanup volledig is) | aparte sessie | done (runbook `docs/runbooks/tenant-delete.md`) |
| 6 | `otplib` (Node) als devDep in `klai-portal/frontend/package.json` | mij | done (otplib 12.0.1) |
| 7 | `playwright.prod.config.ts` met `baseURL = process.env.E2E_BASE_URL` | mij | done |
| 8 | Workflow `.github/workflows/e2e-prod-tenant.yml` | mij | done |

Prerequisite #5 (delete-tenant SPEC) is gemerged, dus de happy-path
J03/J05/J06/J09 cleanup is volledig — die journeys laten geen residual
KB's / templates / transcripts meer achter binnen de e2e-tenant.

**Security-trade-off van #3 skipped:** zonder MFA op de bot is het
wachtwoord de enige auth-factor. Als dat wachtwoord lekt, is het account
direct compromised. Voor een geïsoleerde test-tenant accepteren we dat;
mocht de bot in de toekomst toegang krijgen tot iets gevoeligers, dan
moet #3 alsnog enrolled worden en `E2E_TOTP_SECRET` op `.env.local` /
GitHub Secrets gezet.

---

## 8. CI integration

`.github/workflows/e2e-prod-tenant.yml`:

```yaml
name: E2E prod-tenant

on:
  workflow_run:
    workflows:
      - "Build and push portal-api"
      - "Build and push portal-frontend"
      - "Build and push klai-knowledge-mcp"
      - "Build and push knowledge-ingest"
      - "Build and push retrieval-api"
      - "Sync docker-compose.yml + config + smoke-test to core-01"
    types: [completed]
  schedule:
    - cron: '0 4 * * *'   # nightly 04:00 UTC
  workflow_dispatch:

jobs:
  e2e:
    if: github.event.workflow_run.conclusion == 'success' || github.event_name != 'workflow_run'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v5
        with: { node-version: '24' }
      - name: Install deps
        working-directory: klai-portal/frontend
        run: npm ci
      - name: Install Playwright browsers
        working-directory: klai-portal/frontend
        run: npx playwright install chromium --with-deps
      - name: Run prod-tenant e2e
        working-directory: klai-portal/frontend
        env:
          E2E_BASE_URL: ${{ secrets.E2E_BASE_URL }}
          E2E_USER_EMAIL: ${{ secrets.E2E_USER_EMAIL }}
          E2E_USER_PASSWORD: ${{ secrets.E2E_USER_PASSWORD }}
          E2E_TOTP_SECRET: ${{ secrets.E2E_TOTP_SECRET }}
        run: npx playwright test -c e2e/prod-tenant/_config/playwright.prod.config.ts
      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report-prod-tenant
          path: klai-portal/frontend/playwright-report/
```

**Trigger-keuze:** `workflow_run` op de echte deploy-workflows, niet op
elke push. Reden: e2e draait pas nadat een deploy live is — anders test
hij oude code op live tenant.

---

## 9. Lokale ontwikkeling

Credentials komen uit waar het team bot-secrets bewaart (vraag Mark als
je het niet weet). Voor CI staan ze als **GitHub Actions Secrets**
(`E2E_BASE_URL` / `E2E_USER_EMAIL` / `E2E_USER_PASSWORD` — plus
`E2E_TOTP_SECRET` als #3 ooit enrolled wordt). GitHub Secrets zijn
write-only: niet teruglezbaar, dus geen bron om lokaal uit te kopiëren.

Zet de waarden in `klai-portal/frontend/.env.local` (gitignored via
`*.local`). Een template staat in `e2e/prod-tenant/README.md`. De
`E2E_*`-regels gebruiken `export` zodat het bestand ge-`source`d kan
worden:

```bash
cd klai-portal/frontend
source .env.local

# Run alle journeys
npx playwright test -c e2e/prod-tenant/_config/playwright.prod.config.ts

# Run één journey met UI
npx playwright test e2e/prod-tenant/J03-knowledge.spec.ts --headed

# Debug-mode
PWDEBUG=1 npx playwright test e2e/prod-tenant/J02-chat.spec.ts
```

> `E2E_TOTP_SECRET` is alleen nodig als MFA op de bot enrolled is — dat
> is per 2026-05-15 niet zo. `auth.ts` probet de TOTP-form en skipt de
> stap wanneer hij niet verschijnt, dus laat de regel weg of leeg.
> Mocht MFA later wel enrolled worden: zet de base32-seed in
> `.env.local` (gitignored) — nooit in een chat, ticket of commit.

---

## 10. Failure-modes / debugging

| Symptoom | Eerste check | Tweede check |
|---|---|---|
| J01 TOTP-stap timeout | `E2E_TOTP_SECRET` correct in CI? Tijd-drift op runner? | Login-flow hand-check via prod browser |
| J02 chat geen response | LiteLLM healthy? `docker logs klai-core-litellm-1` | Model-API rate-limit? Kijk in VictoriaLogs `service:litellm AND level:error` |
| J03 RAG-canary mist | Ingestion-status nog `processing` na timeout? | Qdrant collectie aangemaakt? `docker exec klai-core-qdrant-1 curl localhost:6333/collections` |
| J06 scribe transcript leeg | Whisper-server reachable van scribe-api? | gpu-tunnel actief? `pgrep autossh` op core-01 |
| Alle journeys 401 | Auth flow stuk OF e2e-bot account TOTP rotated | Test handmatig via browser; check Zitadel admin |
| Workflow runs maar tests skippen | `workflow_run.conclusion` check faalt | Workflow log checken: triggerende workflow geslaagd? |

Logs altijd aanwezig in:
- Playwright-report HTML artifact (CI)
- VictoriaLogs `request_id:<uuid>` voor server-side trace
- Browser-network-tab in `--headed` mode lokaal

---

## 11. Rollout

Stap voor stap, in volgorde. Elke stap is afgesloten als de check `- [ ]`
groen is.

### Fase 0 — Voor we beginnen ✓

- [x] Dit plan-document is gereviewed en goedgekeurd (2026-05-03)
- [x] Beslissing: scope v1 = J01-J12, geen Focus, J12 (Vexa meeting)
      autonoom via voys-attached mode (§5.1), docs apart
- [x] `E2E_BASE_URL` = `e2e.getklai.com` (isolated-tenant mode)
- [x] **Twee modes** aangenomen (zie §3.1): `isolated-tenant` voor CI,
      `voys-attached` voor lokale runs binnen Voys tenant via Google SSO
- [x] E2E-tenant draait op productie (niet aparte staging)

### Fase 1 — E2E-tenant prerequisites (jij)

- [ ] E2E-tenant aangemaakt op `e2e.getklai.com` via prod signup-flow
- [ ] E2E-user `e2e@getklai.com` ingelogd; TOTP setup met secret bewaard
- [ ] GitHub Secrets ingesteld (4 stuks per §7)
- [ ] Test handmatig: login + TOTP werkt in incognito browser

### Fase 2 — Delete-tenant SPEC (separate sessie)

- [ ] SPEC-INFRA-TENANT-DELETE-001 gestart in nieuwe sessie (prompt al klaar)
- [ ] Endpoint `DELETE /api/admin/org/me` werkt op dev-stack
- [ ] Acceptance criteria AC-1..AC-10 groen
- [ ] Gemerged op main + gedeployed

### Fase 3 — E2E scaffolding (mij)

- [ ] `klai-portal/frontend/e2e/prod-tenant/` directory + `_lib/` + `_config/` aangemaakt
- [ ] `otplib` toegevoegd aan `klai-portal/frontend/package.json` devDeps
- [ ] `playwright.prod.config.ts` werkend met `E2E_BASE_URL`
- [ ] `_lib/auth.ts` werkend, getest met handmatige run
- [ ] `_lib/fixtures.ts` + fixtures gegenereerd

### Fase 4 — Journeys per stuk (mij + jij review)

Per journey één PR. Volgorde van laagste-risico naar hoogste:

- [ ] J01 login (basis voor alles)
- [ ] J11 headers/perf (passief, leunt op J01-storage-state)
- [ ] J07 account/locale (lichte UI-flow)
- [ ] J04 gaps (read-only)
- [ ] J08 admin read-only
- [ ] J10 logout
- [ ] J02 chat round-trip (eerste write-pad, niet-trivial)
- [ ] J05 templates CRUD
- [ ] J06 scribe upload + transcribe
- [ ] J09 admin settings write
- [ ] J03 knowledge + RAG (zwaarste, einde)

Elke journey-PR bevat: spec-file, eventuele helper-uitbreidingen, één
goedgekeurde lokale handmatige run.

### Fase 5 — CI workflow

- [ ] `.github/workflows/e2e-prod-tenant.yml` toegevoegd
- [ ] Eerste nightly-run op schedule slaagt
- [ ] `workflow_run` trigger werkt na een deploy
- [ ] Slack/email-alert ingesteld bij falen (optioneel)

### Fase 6 — Iteratie / uitbreiding

- [ ] Documenteer in `klai-portal/frontend/e2e/prod-tenant/README.md`
      hoe een nieuwe journey toevoegen
- [ ] Opnemen in CLAUDE.md / project rules dat een nieuwe feature een
      e2e-journey vereist
- [ ] (Future) Focus terug → J12-focus.spec.ts
- [ ] (Future) Meetings via Vexa headless mode → J13-meetings
- [ ] (Future) Docs-app eigen e2e-suite

---

## 12. Open vragen

1. **`E2E_BASE_URL` op `e2e.getklai.com` of `staging.getklai.com`?**
   Voorkeur: `e2e.` om aan te geven dat het bot-traffic is, los van
   evt. handmatige staging.
2. **Hoe gaan we om met TOTP-rotatie?** Voorstel: secret blijft stabiel
   tot een security-event. Documenteer in onboarding-runbook.
3. **Mag de e2e-tenant op productie of alleen op staging?** Voorstel:
   productie, want de "prod" in "prod-tenant" is het hele punt — we
   testen wat klanten zien. Risico: bot-traffic gemixt met klant-traffic
   in metrics. Mitigatie: `org_id` filter in product_events query.
4. **Hoe lang behouden we Playwright-rapport HTML?** Voorstel: 14 dagen
   GitHub artifact retention.

---

## 13. Cross-referenties

- Test-pyramid foundation: `.claude/skills/moai/moai-ref-testing-pyramid/`
- Container-hygiene + label-conventie: `.moai/specs/SPEC-INFRA-CONTAINER-HYGIENE-001/`
- Tenant-delete (blocker fase 2): zie nieuwe sessie met
  `SPEC-INFRA-TENANT-DELETE-001` prompt
- Bestaande dev-stack e2e: `klai-portal/frontend/e2e/SPEC-PORTAL-UNIFY-KB-001.spec.ts`
- Bestaande templates e2e: `klai-portal/frontend/tests/e2e/templates.spec.ts`
- VictoriaLogs queries voor debug: `.claude/rules/klai/infra/observability.md`

---

*Plan-versie: 0.1.0 — 2026-05-03 — wordt stap-voor-stap doorgewerkt.*
