# Local Development Setup

Stap-voor-stap handleiding om de Klai portal lokaal te draaien voor development.

> **Laatste end-to-end verificatie:** 2026-05-13 (Mark, brisbane workspace). Alle 8 fixes onderaan in changelog gecaptured. Volg dit runbook letterlijk — werkt op een schone macOS met OrbStack + Node 24 + uv + Python 3.13 in `.venv`.

---

## Architectuuroverzicht

Er zijn drie modi voor lokale development:

### Modus C: Standalone (aanbevolen)

Frontend + backend lokaal, databases in Docker, **geen Zitadel nodig**. Auth wordt volledig gemockt. De backend maakt automatisch een dev user aan bij eerste start.

```
┌─────────────────────────────────────────────────────┐
│  Lokaal (native, hot reload)                        │
│                                                     │
│  ┌──────────────┐     ┌──────────────┐              │
│  │  Frontend     │────▶│  Backend     │              │
│  │  Vite :5174   │     │  FastAPI     │              │
│  │  AUTH_DEV_MODE│     │  :8010       │              │
│  └──────────────┘     └──────┬───────┘              │
│                              │                      │
│   Geen OIDC redirect        │  DB, Redis, etc.     │
│   Geen Zitadel              ▼                      │
│                       ┌──────────────────────────┐  │
│                       │  Docker Compose (dev)    │  │
│                       │                          │  │
│                       │  PostgreSQL  :5434       │  │
│                       │  Redis       :6379       │  │
│                       │  MongoDB     :27017      │  │
│                       │  Meilisearch :7700       │  │
│                       │  LiteLLM     :4000       │  │
│                       └──────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Start:** `make setup && make dev-up && make migrate && make backend` + `make frontend`

Zie [GETTING_STARTED.md](../../GETTING_STARTED.md) voor de volledige quick start (Engels).

### Modus A: Frontend-only

Alleen de frontend draait lokaal. API calls gaan via Vite proxy naar productie. Echte data, echte auth, geen lokale backend/Docker nodig.

```
┌───────────────────────┐          ┌──────────────────────────┐
│  Lokaal                │          │  Productie (core-01)     │
│                       │          │                          │
│  ┌──────────────┐     │  proxy   │  ┌────────────────────┐  │
│  │  Frontend     │────────/api──▶│  │  portal-api        │  │
│  │  Vite :5174   │     │          │  │  PostgreSQL, etc.  │  │
│  └──────┬───────┘     │          │  └────────────────────┘  │
│         │ OIDC        │          │                          │
│         ▼             │          │  ┌────────────────────┐  │
│  ┌──────────────┐     │          │  │  Zitadel           │  │
│  │  auth.get-   │◀────────────────  │  auth.getklai.com  │  │
│  │  klai.com    │     │          │  └────────────────────┘  │
│  └──────────────┘     │          │                          │
└───────────────────────┘          └──────────────────────────┘
```

**Start:** `make frontend` — klaar. Geen Docker, geen backend setup.

### Modus B: Full-stack (voor backend development)

Frontend + backend lokaal, databases in Docker.

```
┌─────────────────────────────────────────────────────┐
│  Lokaal (native, hot reload)                        │
│                                                     │
│  ┌──────────────┐     ┌──────────────┐              │
│  │  Frontend     │────▶│  Backend     │              │
│  │  Vite :5174   │     │  FastAPI     │              │
│  │              │     │  :8010       │              │
│  └──────┬───────┘     └──────┬───────┘              │
│         │                    │                      │
│         │  OIDC              │  DB, Redis, etc.     │
│         ▼                    ▼                      │
│  ┌──────────────┐     ┌──────────────────────────┐  │
│  │  Zitadel     │     │  Docker Compose (dev)    │  │
│  │  (productie) │     │                          │  │
│  │  auth.get-   │     │  PostgreSQL  :5434       │  │
│  │  klai.com    │     │  Redis       :6379       │  │
│  └──────────────┘     │  MongoDB     :27017      │  │
│                       │  Meilisearch :7700       │  │
│                       │  LiteLLM     :4000       │  │
│                       └──────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Start:** `make dev-up && make migrate && make backend` + `make frontend`

---

## Prerequisites

| Tool | Versie | Installatie |
|------|--------|-------------|
| Docker Desktop | 4.x+ | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) |
| Python | 3.12+ | `brew install python@3.12` |
| uv | latest | `brew install uv` of `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 20 LTS+ | `brew install node@20` |
| make | (ingebouwd) | Standaard aanwezig op macOS |

**Zitadel toegang nodig:**
- Toegang tot [auth.getklai.com](https://auth.getklai.com) admin console
- Of de benodigde waarden van een teamlid (ZITADEL_PAT + OIDC Client ID)

---

## Welke modus kiezen?

| Situatie | Modus | Nodig |
|----------|-------|-------|
| Je wilt snel starten zonder externe accounts | **C: Standalone** | Docker + Python + Node |
| Je wilt alleen frontend werken met echte data | **A: Frontend-only** | Node + Zitadel account |
| Je wilt backend testen met echte Zitadel auth | **B: Full-stack** | Docker + Python + Node + Zitadel account |

---

## Snelle start (standalone — Modus C)

De aanbevolen manier om te beginnen — geen Zitadel, geen externe accounts:

```bash
git clone https://github.com/GetKlai/klai.git && cd klai
make setup
make dev-up          # Docker services
make migrate         # Database migraties
make backend         # Backend (terminal 1) — maakt automatisch dev user aan
make frontend        # Frontend (terminal 2)
```

Open [http://localhost:5174](http://localhost:5174) — je bent direct ingelogd als dev user. Geen OIDC redirect, geen Zitadel.

> **AI features:** Voeg `ANTHROPIC_API_KEY=sk-ant-...` toe aan `.env.dev` en herstart Docker: `make dev-down && make dev-up`.

---

## Snelle start (frontend-only — Modus A)

De snelste manier als je Zitadel toegang hebt — geen Docker, geen backend setup:

```bash
# 1. Clone de repo (als je dat nog niet hebt)
git clone https://github.com/GetKlai/klai.git && cd klai

# 2. Eerste setup: kopieert env files, installeert dependencies
make setup

# 3. Vul frontend/.env.development.local in:
#    VITE_OIDC_AUTHORITY=https://auth.getklai.com
#    VITE_OIDC_CLIENT_ID=<oidc-client-id>

# 4. Start de frontend
make frontend
```

Open [http://localhost:5174](http://localhost:5174) — je wordt doorgestuurd naar Zitadel login. Na inloggen werk je met echte productie data via de Vite proxy.

## Snelle start (full-stack — Modus B)

Als je aan de backend werkt met echte Zitadel authenticatie. Lees eerst onderstaande nuances — Mode B vereist meer dan een env-flip, sinds SPEC-AUTH-008 (BFF-pattern) en SPEC-SEC-VALIDATOR-COVERAGE-001 (uitgebreide fail-closed validators) is de set verplichte env vars groter dan Mode C.

```bash
make setup                       # auto-generate keys, copy env, install deps
# Vul backend/.env aan — zie "Configuratie · Stap 2" hieronder voor de volledige lijst.
# Vul frontend/.env.development.local — zie "Configuratie · Stap 3".
# Voeg ZITADEL-redirect-URIs toe op je dev-machine — zie "Zitadel configuratie".
make dev-up                      # Docker services
make migrate                     # Database migraties
make postdeploy                  # Post-deploy SQL (RLS policies + helper functies)
make backend                     # Terminal 1: FastAPI hot-reload
make frontend                    # Terminal 2: Vite HMR
```

Open [http://localhost:5174](http://localhost:5174) — je wordt doorgestuurd naar `my.getklai.com/login`. Log in met je Klai account, je land terug op `localhost:5174/app`.

> **Container-namen volgen de compose project name**, niet `klai-*`. In een canonical checkout `klai/` heten ze `klai-postgres-1` etc. In een Conductor workspace `brisbane/` zijn het `brisbane-postgres-1`. Gebruik `docker ps` om de exacte naam te vinden voor seed-commando's hieronder.

> **Let op:** Lokale DB is leeg. Je Zitadel-account moet handmatig in `portal_users` worden gezaaid, anders krijgt elke API-call 401 na login. Pas eerst de placeholder in en run:
>
> ```bash
> # Vereist: ZITADEL_PORTAL_ORG_ID, ZITADEL_USER_ID, EMAIL en POSTGRES_CONTAINER ingevuld
> # in je shell. Haal ze uit klai-portal/backend/.env + Management API search (zie onder).
> ORG_ID="$(grep '^ZITADEL_PORTAL_ORG_ID=' klai-portal/backend/.env | cut -d= -f2-)"
> USER_ID="<jouw_zitadel_user_id>"
> EMAIL="<jouw_email>"
> POSTGRES=$(docker ps --format '{{.Names}}' | grep postgres-1 | head -1)
>
> docker exec -i "$POSTGRES" psql -U klai -d klai <<EOF
> INSERT INTO portal_orgs (zitadel_org_id, name, slug, plan, provisioning_status, primary_domain)
> VALUES ('${ORG_ID}', 'Dev Org', 'dev', 'professional', 'ready', 'localhost')
> ON CONFLICT (zitadel_org_id) DO UPDATE SET name = EXCLUDED.name;
>
> INSERT INTO portal_users (zitadel_user_id, org_id, role, display_name, email, status, created_at)
> SELECT '${USER_ID}', id, 'admin'::portal_user_role, 'Jouw Naam', '${EMAIL}', 'active', now()
> FROM portal_orgs WHERE zitadel_org_id = '${ORG_ID}'
> ON CONFLICT (zitadel_user_id, org_id) DO UPDATE SET status = 'active';
> EOF
> ```
>
> Drie gotcha's t.o.v. een oudere versie van dit runbook:
> 1. `provisioning_status` MOET `'ready'` zijn (niet `'complete'` — die waarde bestaat niet in de CHECK constraint).
> 2. `portal_users` heeft **geen** `updated_at` kolom — die niet meegeven of de migrate-versie loopt vooruit op deze runbook.
> 3. De unique constraint op `portal_users` is `(zitadel_user_id, org_id)`, niet alleen `zitadel_user_id` (sinds SPEC-PORTAL-PROFILES-001 — multi-org support).
>
> Je Zitadel user_id vind je via Management API (vereist `ZITADEL_PAT` in backend `.env`):
> ```bash
> PAT=$(grep '^ZITADEL_PAT=' klai-portal/backend/.env | cut -d= -f2-)
> curl -s -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' \
>   "https://auth.getklai.com/management/v1/users/_search" \
>   -d '{"queries":[{"displayNameQuery":{"displayName":"Jouw Naam","method":"TEXT_QUERY_METHOD_CONTAINS"}}]}' \
>   | python3 -c "import json,sys; [print(u['id'],u['human']['profile']['displayName']) for u in json.load(sys.stdin).get('result',[])]"
> ```

---

## Configuratie

### Stap 1: Docker services (.env.dev)

Open `.env.dev` en vul je LLM API key in:

```bash
ANTHROPIC_API_KEY=sk-ant-...    # Verplicht voor AI features
```

De overige waarden (database wachtwoorden etc.) hebben werkende defaults.

### Stap 2: Backend (klai-portal/backend/.env)

`make setup` kopieert `.env.example` en genereert de drie encryption keys automatisch. Voor **Mode C (standalone)** ben je daarmee klaar — niets meer doen. Voor **Mode B (productie Zitadel)** moeten ook deze velden gezet zijn (de fail-closed validators uit SPEC-SEC-VALIDATOR-COVERAGE-001 weigeren te booten zonder):

```bash
# Mode-B switch: zet AUTH_DEV_MODE=false (default in template is true voor Mode C)
DEBUG=true
AUTH_DEV_MODE=false
PORTAL_ENV=development          # Anders blokkeert _no_debug_in_production de boot
DOMAIN=localhost                 # Anders rejected _check_mock_billing_vs_domain de MOCK_BILLING=true
MOCK_BILLING=true                # Geen Moneybird vereist
FRONTEND_URL=http://localhost:5174
CORS_ORIGINS=http://localhost:5174

# Zitadel — alle waarden komen uit prod /opt/klai/.env. Snelste ophaalmethode:
#   ssh core-01 'grep -E "^(PORTAL_API_ZITADEL_PAT|PORTAL_API_ZITADEL_PORTAL_CLIENT_SECRET|ZITADEL_PROJECT_ID|ZITADEL_PORTAL_ORG_ID|ZITADEL_PORTAL_CLIENT_ID|ZITADEL_IDP_GOOGLE_ID|ZITADEL_IDP_MICROSOFT_ID)=" /opt/klai/.env'
# Of via SOPS:
#   cd klai-infra && SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops -d core-01/.env.sops | grep '^...'
ZITADEL_BASE_URL=https://auth.getklai.com
ZITADEL_PAT=<PORTAL_API_ZITADEL_PAT uit prod>
ZITADEL_PROJECT_ID=<ZITADEL_PROJECT_ID uit prod>
ZITADEL_PORTAL_ORG_ID=<ZITADEL_PORTAL_ORG_ID uit prod>
ZITADEL_PORTAL_CLIENT_ID=<ZITADEL_PORTAL_CLIENT_ID uit prod>     # OIDC client_id van "Klai Portal BFF"
ZITADEL_PORTAL_CLIENT_SECRET=<PORTAL_API_ZITADEL_PORTAL_CLIENT_SECRET uit prod>
ZITADEL_IDP_GOOGLE_ID=<ZITADEL_IDP_GOOGLE_ID uit prod>
ZITADEL_IDP_MICROSOFT_ID=<ZITADEL_IDP_MICROSOFT_ID uit prod>

# Internal service-to-service secrets — kunnen random lokaal zijn (niet prod-waarden!).
# Local backend praat niet met klai-connector/knowledge-ingest/etc, deze satisfy alleen validators.
INTERNAL_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
KLAI_CONNECTOR_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
KNOWLEDGE_INGEST_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
RETRIEVAL_API_INTERNAL_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
DOCS_INTERNAL_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
BFF_SESSION_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
VEXA_WEBHOOK_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
MONEYBIRD_WEBHOOK_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# MCP OAuth — local URLs voldoen voor _require_mcp_oauth_urls
MCP_OAUTH_ISSUER_BASE_URL=http://localhost:8010
MCP_OAUTH_RESOURCE_URL=http://localhost:8010
```

> **Let op (genereerde keys):** `PORTAL_SECRETS_KEY`, `ENCRYPTION_KEY` en `SSO_COOKIE_KEY` zijn alle drie verplicht (32 bytes elk — eerste twee hex-encoded, derde Fernet/urlsafe-base64). `make setup` regelt ze automatisch. Zonder een van de drie crasht de backend met bv. `AES-256 requires a 32-byte key, got 0 bytes`.

> **Waarom 8 random secrets voor `_require_*`?** Sinds SPEC-SEC-VALIDATOR-COVERAGE-001 weigert de backend te booten als een service-to-service Bearer-token leeg is (anders fail-open auth). Lokaal is dat overdreven maar onomzeilbaar — de validators kennen geen "ik draai local"-mode. Random local strings is fine; ze worden alleen gebruikt als outbound auth header naar services die we niet draaien.

> **Var-naam mismatch in `.env.example`:** Een eerdere versie van de template noemde `ZITADEL_PORTAL_APP_ID` — die var wordt nergens door de backend gelezen. De pydantic-settings field heet `zitadel_portal_client_id` → env-var `ZITADEL_PORTAL_CLIENT_ID`. Negeer een legacy `ZITADEL_PORTAL_APP_ID=` regel als je 'm tegenkomt.

### Stap 3: Frontend (klai-portal/frontend/.env.development.local)

`make setup` kopieert `.env.local.example` naar `.env.development.local`.
Voor **Mode C (standalone)** is de default goed. Voor **Mode B (productie
Zitadel)**, vervang met:

```bash
VITE_AUTH_DEV_MODE=false
VITE_OIDC_AUTHORITY=https://auth.getklai.com
VITE_OIDC_CLIENT_ID=<ZITADEL_PORTAL_CLIENT_ID uit prod>
VITE_API_PROXY_TARGET=http://localhost:8010
```

> **Let op:** `VITE_OIDC_CLIENT_ID` is de **OIDC Client ID** van de "Klai Portal BFF" app (huidige BFF-pattern sinds SPEC-AUTH-008). Die waarde leeft NIET in dit runbook — haal 'm uit prod via `ssh core-01 'grep ZITADEL_PORTAL_CLIENT_ID /opt/klai/.env'`. Verwar 'm niet met de Zitadel App ID (intern identifier, niet de waarde die de browser gebruikt). Een oudere versie van dit runbook gebruikte een pre-BFF client_id — die heeft de signin-knop allang vervangen.

> **Vite herstart vereist bij env-wijzigingen:** In tegenstelling tot de backend pikt Vite `.env.development.local` wijzigingen pas op na een volledige herstart (`Ctrl+C` → `npm run dev`). Hot reload werkt niet voor env vars.

> **E2E credentials:** `klai-portal/frontend/.env.local` kan productie-E2E
> credentials bevatten. Gebruik dat bestand niet als lokale Vite-dev config.
> Run voor browserchecks altijd eerst
> `scripts/local-dev-status.sh --mode local --strict`.

---

## Auth Dev Mode

Auth Dev Mode bypast Zitadel OIDC volledig — je bent direct ingelogd zonder browser redirect. Dit is de standaard in Modus C.

### Hoe het werkt

Bij Modus C (standalone) hoef je **niets te doen** — `make setup` configureert Auth Dev Mode automatisch:

- Backend: `AUTH_DEV_MODE=true` + `DEBUG=true` + `AUTH_DEV_USER_ID=dev-user-1`
- Frontend: `VITE_AUTH_DEV_MODE=true`
- De backend maakt automatisch een dev org ("Dev Organization", slug: `dev`) en dev user (`dev-user-1`) aan bij eerste start

### Eigen user ID gebruiken (optioneel, voor core developers)

Als je liever je eigen Zitadel user ID gebruikt (bijv. om met productie Zitadel te kunnen wisselen):

In `klai-portal/backend/.env`:
```bash
AUTH_DEV_MODE=true
AUTH_DEV_USER_ID=<jouw_zitadel_user_id>
```

Je moet dan zelf een user met dat ID in de lokale DB aanmaken, of `make seed` aanpassen.

> **Vereiste:** Backend vereist `AUTH_DEV_MODE=true` én `DEBUG=true` tegelijk. Zonder `DEBUG=true` werkt de bypass niet.

> **Nooit in productie:** De backend logt een grote waarschuwing als Auth Dev Mode actief is. Commit deze waarden nooit naar git.

---

## Zitadel configuratie

Omdat de lokale dev omgeving tegen de productie Zitadel draait, zijn er twee dingen nodig:

### ZITADEL_PAT ophalen (backend)

1. Ga naar [auth.getklai.com/ui/console](https://auth.getklai.com/ui/console)
2. Users > Service Accounts > **Portal API**
3. Personal Access Tokens > kopieer een bestaand token
4. Of maak een nieuw token aan (+ New)

**Alternatief:** Vraag het aan een teamlid, of decrypt uit `klai-infra`:
```bash
cd klai-infra
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops -d core-01/.env.sops | grep PORTAL_API_ZITADEL_PAT
```

### VITE_OIDC_CLIENT_ID ophalen (frontend)

1. Ga naar [auth.getklai.com/ui/console](https://auth.getklai.com/ui/console)
2. Projects > **Klai Platform** > Applications > **Klai Portal**
3. Kopieer de **Client ID**

### Redirect URIs en Dev Mode

De OIDC app **"Klai Portal BFF"** (post-SPEC-AUTH-008 BFF-pattern) heeft per default alleen prod-redirect-URIs. Voor lokale Mode B moet `http://localhost:5174/api/auth/oidc/callback` toegevoegd zijn aan `redirectUris`.

Ophalen van project_id + app_id (eenmalig, in shell):
```bash
PAT=$(grep '^ZITADEL_PAT=' klai-portal/backend/.env | cut -d= -f2-)
PROJ=$(grep '^ZITADEL_PROJECT_ID=' klai-portal/backend/.env | cut -d= -f2-)
APP=$(curl -s -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' \
  "https://auth.getklai.com/management/v1/projects/$PROJ/apps/_search" -d '{}' \
  | python3 -c "import json,sys; [print(a['id']) for a in json.load(sys.stdin).get('result',[]) if 'BFF' in a.get('name','')]")
echo "PROJ=$PROJ APP=$APP"
```

Verifieer huidige config:
```bash
curl -s -H "Authorization: Bearer $PAT" \
  "https://auth.getklai.com/management/v1/projects/$PROJ/apps/$APP" \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['app']['oidcConfig']; \
  print('redirectUris:', d.get('redirectUris')); \
  print('postLogoutRedirectUris:', d.get('postLogoutRedirectUris')); \
  print('devMode:', d.get('devMode'))"
```

Verwacht:
- `redirectUris` bevat `http://localhost:5174/api/auth/oidc/callback`
- `postLogoutRedirectUris` bevat `http://localhost:5174/logged-out`
- `devMode: True`

**Als één van de drie ontbreekt** (eerste keer dat iemand Mode B doet op een schone Zitadel-app):

Optie 1 — Via Console (handmatig, één-malig):
1. [auth.getklai.com/ui/console](https://auth.getklai.com/ui/console) → Projects → **Klai Platform** → Applications → **Klai Portal BFF**
2. Tab **Redirect URIs** → voeg toe `http://localhost:5174/api/auth/oidc/callback`
3. Tab **Post Logout URIs** → voeg toe `http://localhost:5174/logged-out`
4. Onder **OIDC Configuration** → toggle **Dev Mode** aan (vereist voor `http://`)
5. Save

Optie 2 — Via Management API (idempotent script — `PAT`, `PROJ`, `APP` als hierboven gezet):
```bash
# Backup huidige config (kun je gebruiken om terug te draaien)
curl -s -H "Authorization: Bearer $PAT" \
  "https://auth.getklai.com/management/v1/projects/$PROJ/apps/$APP" \
  > /tmp/zitadel-app-backup.json

# Bouw nieuwe config + push
python3 <<'PY' > /tmp/zitadel-put-body.json
import json
d = json.load(open('/tmp/zitadel-app-backup.json'))
c = d['app']['oidcConfig']
body = {
    "redirectUris": sorted(set(c.get('redirectUris', []) + ['http://localhost:5174/api/auth/oidc/callback'])),
    "responseTypes": c.get('responseTypes', ['OIDC_RESPONSE_TYPE_CODE']),
    "grantTypes": c.get('grantTypes', []),
    "appType": "OIDC_APP_TYPE_WEB",
    "authMethodType": c.get('authMethodType', 'OIDC_AUTH_METHOD_TYPE_POST'),
    "postLogoutRedirectUris": sorted(set(c.get('postLogoutRedirectUris', []) + ['http://localhost:5174/logged-out'])),
    "version": "OIDC_VERSION_1_0",
    "devMode": True,
    "accessTokenType": c.get('accessTokenType', 'OIDC_TOKEN_TYPE_JWT'),
    "accessTokenRoleAssertion": c.get('accessTokenRoleAssertion', True),
    "idTokenRoleAssertion": c.get('idTokenRoleAssertion', True),
    "idTokenUserinfoAssertion": c.get('idTokenUserinfoAssertion', True),
    "clockSkew": c.get('clockSkew', '0s'),
    "additionalOrigins": [],
}
print(json.dumps(body))
PY

curl -s -X PUT -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' \
  "https://auth.getklai.com/management/v1/projects/$PROJ/apps/$APP/oidc_config" \
  -d @/tmp/zitadel-put-body.json
```

Het script is idempotent — herhaalde runs zetten dezelfde state.

> **Waarom devMode aan moet:** Zitadel weigert per default `http://` redirect URIs te registreren (TLS-only). devMode loosens dat ALLEEN voor de registratie-lijst — exact-match validatie op authorize-tijd is ongewijzigd. Risk: aanvaller met de prod client_id kan een redirect uitlokken naar `http://localhost:5174/api/auth/oidc/callback`, maar landt alleen op de loopback van het slachtoffer zelf (bounded, niet exfiltrerend).

> **Productie-impact:** geen. De bestaande prod URIs (`https://dev.getklai.com/...`, `https://my.getklai.com/...`) blijven onveranderd in de lijst.

---

## Makefile targets

| Commando | Omschrijving |
|----------|-------------|
| `make help` | Toon alle beschikbare targets |
| `make setup` | Eerste setup: kopieer env files, genereer encryption keys, installeer dependencies |
| `make dev-up` | Start Docker services |
| `make dev-down` | Stop Docker services (data blijft behouden) |
| `make dev-reset` | Stop services EN verwijder alle data (schone start) |
| `make dev-status` | Toon status van Docker services |
| `make dev-logs` | Volg logs van alle Docker services |
| `make backend` | Start FastAPI backend met hot reload (:8010) |
| `make frontend` | Start Vite dev server (:5174, of `$CONDUCTOR_PORT` in Conductor) |
| `make local-dev-status` | Toon/valideer lokale standalone browser-test setup |
| `make e2e-prod-status` | Toon/valideer productie-E2E setup |
| `make migrate` | Draai Alembic database migraties |
| `make postdeploy` | Apply post-deploy SQL files (RLS policies + helper functies, klai-superuser) |
| `make seed` | Seed demo-data (dev org + users) in lokale DB |
| `make lint` | Draai linters (ruff + eslint) |
| `make check` | Draai type checks (pyright + tsc) |

> **Compose project name = working directory naam.** Docker Compose leidt de project name af van `pwd` als geen `COMPOSE_PROJECT_NAME` is gezet. Containers krijgen daardoor namen als `<dir>-postgres-1`, `<dir>-redis-1`, etc. In de canonical `klai/` checkout zijn dat `klai-postgres-1`; in een Conductor workspace `brisbane/` zijn dat `brisbane-postgres-1`. Voorbeelden in dit runbook (zoals seed-SQL) gebruiken `klai-*` — vervang dat met `$(docker ps --format '{{.Names}}' | grep postgres-1)` als je in een ander-dir-naam checkout zit.

> **Parallelle Conductor workspaces**: `make frontend` gebruikt
> `$CONDUCTOR_PORT` als frontend-port wanneer Conductor die zet; `make backend`
> gebruikt dan `$CONDUCTOR_PORT+1`. Docker Compose services gebruiken nog vaste
> host-poorten uit `docker-compose.dev.yml` (5434/6379/7700/etc.). Stop de
> containers in workspace A vóór je `make dev-up` doet in workspace B, of maak
> de compose host-poorten workspace-specifiek.

---

## Troubleshooting

### Port conflict

```
Error: Bind for 0.0.0.0:5434 failed: port is already allocated
```

Een andere PostgreSQL draait al op die poort. Check wat er draait:
```bash
lsof -nP -iTCP:5434 -sTCP:LISTEN
```

> **Opmerking:** Klai dev gebruikt poort **5434** (niet de standaard 5432) om conflicten met andere lokale PostgreSQL instances te voorkomen.

### `make setup` faalt op verse macOS met `No module named 'cryptography'`

Symptoom:
```
==> Copying environment files...
  Generating encryption keys...
Traceback (most recent call last):
  ModuleNotFoundError: No module named 'cryptography'
make: *** [setup] Error 1
```

Oorzaak: het `setup`-target roept `python3 -c "from cryptography.fernet import Fernet; ..."` om `SSO_COOKIE_KEY` te genereren. macOS' systeem-`python3` (3.9) heeft `cryptography` niet uit de doos.

Workaround (één keer): genereer de drie keys handmatig met alleen stdlib + sed ze in `.env`:
```bash
cd klai-portal/backend
SECRETS_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
ENCRYPT_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
COOKIE_KEY=$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
sed -i.bak "s|^PORTAL_SECRETS_KEY=.*|PORTAL_SECRETS_KEY=$SECRETS_KEY|" .env
sed -i.bak "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=$ENCRYPT_KEY|" .env
sed -i.bak "s|^SSO_COOKIE_KEY=.*|SSO_COOKIE_KEY=$COOKIE_KEY|" .env
rm -f .env.bak
```
Vervolgens `cd .. && uv sync` en `cd frontend && npm install` om de rest van setup af te ronden.

Strukturele fix (nog uit te voeren): Makefile `setup`-target moet stdlib-only zijn — Fernet keys zijn 32 random bytes urlsafe-base64 encoded, identiek aan wat `Fernet.generate_key()` returnt.

### Backend start mislukt: "ZITADEL_PAT required" / "AES-256 requires a 32-byte key" / "Missing required: <X>_SECRET"

De `.env` file mist verplichte velden. Sinds SPEC-SEC-VALIDATOR-COVERAGE-001 zijn er veel meer fail-closed validators dan de oude vijf. Run de complete check:
```bash
cd klai-portal/backend
for v in ZITADEL_PAT DATABASE_URL PORTAL_SECRETS_KEY ENCRYPTION_KEY SSO_COOKIE_KEY \
         INTERNAL_SECRET KLAI_CONNECTOR_SECRET KNOWLEDGE_INGEST_SECRET \
         RETRIEVAL_API_INTERNAL_SECRET DOCS_INTERNAL_SECRET BFF_SESSION_KEY \
         VEXA_WEBHOOK_SECRET MONEYBIRD_WEBHOOK_TOKEN \
         MCP_OAUTH_ISSUER_BASE_URL MCP_OAUTH_RESOURCE_URL \
         PORTAL_ENV DOMAIN; do
  v=$(grep "^${v}=" .env | cut -d= -f2-)
  [ -z "$v" ] && echo "MISSING: $v"
done
```
Elk MISSING-veld geeft een specifieke validator-error bij boot. Random hex (`python3 -c "import secrets; print(secrets.token_hex(32))"`) is goed voor alle `*_SECRET` velden in Mode B local-dev — ze worden alleen gebruikt voor service-to-service auth headers naar services die we lokaal niet draaien.

### Frontend login redirect mislukt: "Errors.App.NotFound"

**Symptoom:** Na klikken op "Inloggen" stuurt Zitadel `{"error":"invalid_request","error_description":"Errors.App.NotFound"}`.

**Oorzaak:** `VITE_OIDC_CLIENT_ID` bevat de App ID in plaats van de OIDC Client ID. Beide zijn 18-cijferige nummers maar zijn **niet** hetzelfde.

**Fix:** Gebruik `<oidc-client-id>` (OIDC Client ID), niet `<zitadel-app-id>` (App ID).

Verifieer via Zitadel API:
```bash
curl -s -H "Authorization: Bearer $ZITADEL_PAT" \
  "https://auth.getklai.com/management/v1/projects/<zitadel-project-id>/apps/_search" \
  -d '{}' | grep -o '"clientId":"[^"]*"\|"name":"[^"]*"'
```

**Herstart Vite na de wijziging** — env vars worden niet hot-reloaded.

### Frontend login stuurt door naar live app

**Symptoom:** Na Zitadel login kom je uit op `getklai.getklai.com` in plaats van `localhost:5174`.

**Oorzaak 1:** Vite is niet herstart na `.env.development.local` wijziging — de oude config is nog actief.
**Oorzaak 2:** `VITE_AUTH_DEV_MODE=true` staat nog aan — zet deze uit voor echte OIDC.

**Fix:** Zorg dat `.env.development.local` er zo uitziet en herstart Vite:
```bash
# VITE_AUTH_DEV_MODE=true    ← uitgecommentarieerd
VITE_OIDC_AUTHORITY=https://auth.getklai.com
VITE_OIDC_CLIENT_ID=<oidc-client-id>
```

### LiteLLM start niet op

LiteLLM wacht tot PostgreSQL healthy is. Check:
```bash
make dev-status     # Zijn alle services healthy?
make dev-logs       # Bekijk LiteLLM logs
```

Als PostgreSQL niet start, controleer of poort 5434 vrij is.

### `make migrate` faalt op fresh DB met `column "visibility" of relation "portal_knowledge_bases" does not exist`

**Symptoom:** Eerste `make migrate` na `make dev-reset` faalt halverwege. Hele upgrade-transactie rolt terug, `alembic_version` blijft leeg, DB heeft geen tabellen.

**Oorzaak:** Een migratie (`z3a4b5c6d7e8_backfill_default_kbs.py`) doet INSERT met forward-column-referenties naar kolommen (`visibility`, `docs_enabled`, `owner_type`) die pas door latere migraties worden toegevoegd. Op prod werkte het ooit omdat de chain historisch via parallelle branches landde — op fresh install klopt de topologische volgorde niet. Default KBs worden inmiddels lazy gemaakt door `app.services.default_knowledge_bases`, dus de migratie-backfill is overbodig op nieuwe installs.

**Fix:** de migratie is in main aangepast tot een no-op `upgrade()` (met behouden `downgrade()`). Pull main / rebase op een tip die deze fix bevat.

### `make migrate` faalt op fresh DB met `constraint "portal_users_zitadel_user_id_key" of relation "portal_users" does not exist`

**Oorzaak:** `c3d4e5f6g7h8_portal_users_multi_org.py` deed `op.drop_constraint(...)` op een constraint-naam die alleen historisch op prod bestond (parent migratie creëert het als unique INDEX, niet als CONSTRAINT). Fresh installs hebben de constraint nooit gehad → drop faalt.

**Fix:** in main is de `drop_constraint(...)` vervangen door `op.execute("ALTER TABLE portal_users DROP CONSTRAINT IF EXISTS portal_users_zitadel_user_id_key")` — idempotent op beide states. Pull main.

### `make migrate` faalt met `role "portal_api" does not exist` of `role "grafana_reader" does not exist`

**Oorzaak:** Migraties referencen Postgres-rollen die in prod door provisioning worden aangemaakt maar in `dev/postgres-init.sql` niet werden gecreëerd.

**Fix:** in main creëert `dev/postgres-init.sql` deze rollen automatisch op DB-init (NOLOGIN — ze hoeven lokaal niets te kunnen, alleen bestaan zodat GRANT-statements parsen). Vereist `make dev-reset` om opnieuw te initialiseren als je een DB had vóór de fix.

### Post-deploy SQL faalt op fresh DB met `function public._rls_current_org_id() does not exist`

**Oorzaak:** `scripts/apply_post_deploy_sql.sh` applieert files alfabetisch. `post_deploy_85e5d0a7cb98_kb_uploads_rls.sql` gebruikt de helper `_rls_current_org_id()`, maar die wordt aangemaakt door `post_deploy_rls_raise_on_missing_context.sql` — alfabetisch later. Op prod werkt het omdat de helper al door een eerdere deploy aangemaakt is.

**Workaround (totdat helper-script `--local` mode + dependency-aware ordering krijgt):**
```bash
cd klai-portal/backend
# 1. Bootstrap de helper-functie EERST:
docker exec -i $(docker ps --format '{{.Names}}' | grep postgres-1 | head -1) psql -U klai -d klai \
  -v ON_ERROR_STOP=1 < alembic/versions/post_deploy_rls_raise_on_missing_context.sql
# 2. Daarna alfabetisch alle post-deploy SQLs (rls_raise loopt nog een keer idempotent):
for f in alembic/versions/post_deploy_*.sql; do
  [[ "$(basename "$f")" == *_rollback_* ]] && continue
  docker exec -i $(docker ps --format '{{.Names}}' | grep postgres-1 | head -1) psql -U klai -d klai -v ON_ERROR_STOP=1 < "$f" || break
done
```

Een proper `make postdeploy` target met deze logica wordt toegevoegd in de runbook-PR.

### Mode B: login redirect blijft hangen op `my.getklai.com/login` of belandt op `voys.getklai.com/app`

**Symptoom:** Na clicking "Log in" op `localhost:5174` open je `my.getklai.com/login?authRequest=...`, daarna land je op `voys.getklai.com/app` in plaats van `localhost:5174/app`. Backend logs tonen geen `/api/auth/oidc/callback` request.

**Oorzaak:** Je browser heeft een actieve prod-`klai_sso` cookie op `my.getklai.com` (van eerder inloggen op productie). De Zitadel login-UI doet `POST /api/auth/sso-complete` met dat cookie en finalizet de authRequest server-side via prod-backend. De returned `callback_url` zou `http://localhost:5174/...` moeten zijn maar de mixed-content + cross-origin race in Chrome breekt de hop.

**Fix:** test Mode B in een **incognito-venster** (of een browser-profiel zonder prod-Klai-session). De flow werkt schoon zonder de prod-SSO-hijack. Voor regulier dev-gebruik: blijf in Mode C — die heeft dit probleem niet.

### Database migratie mislukt: "Multiple head revisions"

**Symptoom:** `alembic upgrade head` geeft `ERROR Multiple head revisions are present`.

**Oorzaak:** Twee migratiebestanden hebben dezelfde `revision` ID.

**Fix:**
```bash
# Zoek het duplicaat
grep -r "^revision" klai-portal/backend/alembic/versions/ | sort | uniq -d -f1

# Genereer een uniek nieuw revision ID
cd klai-portal/backend && uv run python -c "import uuid; print(uuid.uuid4().hex[:12])"

# Pas het duplicaat aan: revision + down_revision + bestandsnaam
```

### Database migratie mislukt: "column does not exist"

**Symptoom:** Backend start maar geeft 500 errors. Logs tonen `UndefinedColumnError: column X does not exist`.

**Oorzaak:** Het SQLAlchemy model heeft een nieuwe kolom die nog niet in een Alembic migratie zit.

**Fix:**
```bash
# Maak een handmatige migratie
cd klai-portal/backend
uv run alembic revision -m "add_missing_column"
# Vul upgrade/downgrade handmatig in — autogenerate werkt niet altijd door FK volgorde
uv run alembic upgrade head
```

### Database migratie mislukt (algemeen)

```bash
# Reset de database volledig
make dev-reset
make dev-up
# Wacht 10 seconden tot PostgreSQL healthy is
make migrate
```

### Backend port 8010 in gebruik na crash (Windows)

**Symptoom:** Nieuwe backend start maar bindt niet: `[WinError 10048] only one usage of each socket address`.

**Oorzaak:** Een eerder uvicorn proces (reloader) houdt de socket vast, ook na een crash. Standaard `taskkill` werkt niet altijd.

**Fix:**
```bash
# Zoek het PID dat poort 8010 vasthoudt
powershell -Command "Get-NetTCPConnection -LocalPort 8010 | Select-Object State,OwningProcess"

# Kill het process (vervang 12345 door het gevonden PID)
powershell -Command "Stop-Process -Id 12345 -Force"

# Als dat niet werkt — start op een andere port
uv run uvicorn app.main:app --host 0.0.0.0 --port 8011
# Update VITE_API_PROXY_TARGET in frontend/.env.development.local mee
```

### Alles resetten (nucleaire optie)

```bash
make dev-reset      # Verwijdert alle Docker volumes
make dev-up         # Start met schone databases
make migrate        # Draai migraties opnieuw
```

---

## Dagelijkse workflow

```bash
# Begin van de dag
make dev-up          # Start Docker services (als ze niet draaien)

# Development (twee terminals)
make backend         # Terminal 1: FastAPI met hot reload
make frontend        # Terminal 2: Vite met HMR

# Einde van de dag
make dev-down        # Stop Docker services (data blijft)
```

---

## Testen

Controleer na het opstarten of de volledige login flow werkt:

1. Open [http://localhost:5174](http://localhost:5174) in je browser
2. Je wordt automatisch doorgestuurd naar `auth.getklai.com` (Zitadel login)
3. Log in met je Klai account
4. Na het inloggen keer je terug naar `localhost:5174` — je ziet het portal dashboard

Als de redirect mislukt, controleer:
- Draait de frontend? (`make frontend`)
- Is `VITE_OIDC_CLIENT_ID` correct ingevuld in `klai-portal/frontend/.env.development.local`?
- Staat Dev Mode aan op de Zitadel app? (zie "Redirect URIs en Dev Mode" hierboven)

---

## Wat draait er niet lokaal?

| Service | Waarom niet | Impact |
|---------|-------------|--------|
| **Zitadel** | Complex om lokaal op te zetten; productie werkt prima voor auth | Geen — login werkt via productie |
| **Caddy** | Reverse proxy niet nodig; frontend praat direct met backend | Geen |
| **LibreChat** | Per-tenant containers; niet nodig voor portal development | Chat features niet beschikbaar |
| **Monitoring** | Grafana, VictoriaMetrics — alleen nodig voor ops | Geen |
| **Vexa** | Meeting bot infrastructure — apart project | Meeting features niet beschikbaar |
| **Knowledge stack** | Qdrant, TEI, FalkorDB — alleen voor KB features | Knowledge base features niet beschikbaar |

---

## Zie ook

- [.env.dev.example](../../.env.dev.example) — Docker services environment template
- [klai-portal/backend/.env.example](../../klai-portal/backend/.env.example) — Backend environment template
- [klai-portal/frontend/.env.local.example](../../klai-portal/frontend/.env.local.example) — Frontend development environment template (copy to `.env.development.local`)
- [agent-browser-testing.md](agent-browser-testing.md) — Agent/browser/E2E preflight contract
- [docker-compose.dev.yml](../../docker-compose.dev.yml) — Docker Compose configuratie
- [dev/postgres-init.sql](../../dev/postgres-init.sql) — Postgres init script (creëert klai DB + litellm DB + portal_api/grafana_reader rollen)

---

## Changelog — local-dev verificatie 2026-05-13

End-to-end fresh-install run met OrbStack + uv + npm onthulde 8 issues die het runbook (of de tooling) blokkeerden. Onderstaand wat is/wordt opgelost:

| # | Issue | Resolutie |
|---|---|---|
| 1 | `make setup` faalt op verse macOS zonder system-`cryptography` (`SSO_COOKIE_KEY`-stap crasht) | Runbook: workaround in troubleshooting. Tooling: Makefile moet stdlib-only (`base64.urlsafe_b64encode(os.urandom(32))`) — open follow-up. |
| 2 | Migratie `z3a4b5c6d7e8_backfill_default_kbs.py` INSERT met forward-column refs blokkeert fresh-DB upgrade | Upgrade-body no-op gemaakt; defaults gaan via lazy `app.services.default_knowledge_bases`. |
| 3 | Migratie `c3d4e5f6g7h8_portal_users_multi_org.py` `drop_constraint(...)` faalt op fresh DB | `DROP CONSTRAINT IF EXISTS` via raw SQL. |
| 4 | `dev/postgres-init.sql` miste `portal_api` + `grafana_reader` rollen | Beide toegevoegd als `NOLOGIN`. |
| 5 | `apply_post_deploy_sql.sh` SSH-only naar core-01; alfabetische volgorde dependency-broken | Workaround in troubleshooting; nette `make postdeploy` + `--local` flag in follow-up. |
| 6 | `.env.example` had verouderde var-naam `ZITADEL_PORTAL_APP_ID` (backend leest `_CLIENT_ID`) | Runbook explicit; .env.example update in follow-up. |
| 7 | Mode B vereist 14+ env vars meer dan de 5 die het runbook noemde (`INTERNAL_SECRET`, `KLAI_CONNECTOR_SECRET`, `BFF_SESSION_KEY`, `MCP_OAUTH_*`, etc.) | Complete lijst in Stap 2; troubleshoot-blok met grep-checker. |
| 8 | Zitadel "Klai Portal BFF" app had geen `localhost` redirect URI + devMode uit | Toegevoegd via Management API (een-malig); script in "Redirect URIs en Dev Mode". |

Plus drie kleinere runbook-bugs gefixt: seed `provisioning_status` moet `'ready'` zijn (niet `'complete'`), `portal_users` heeft geen `updated_at` kolom, en de unique constraint is `(zitadel_user_id, org_id)` sinds multi-org support.
