# Local Development Setup

Stap-voor-stap handleiding om de Klai portal lokaal te draaien voor development.

---

## Architectuuroverzicht

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

**Wat lokaal draait:** Frontend (Vite), Backend (FastAPI), databases en LiteLLM in Docker.
**Wat NIET lokaal draait:** Zitadel (productie), Caddy, LibreChat, monitoring stack.

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

## Snelle start

```bash
# 1. Clone de repo (als je dat nog niet hebt)
git clone https://github.com/GetKlai/klai.git && cd klai

# 2. Eerste setup: kopieert env files, installeert dependencies
make setup

# 3. Vul de configuratie in (zie sectie hieronder)
#    - .env.dev
#    - klai-portal/backend/.env
#    - klai-portal/frontend/.env.local

# 4. Start alles
make dev-up          # Docker services
make migrate         # Database migraties
make backend         # Backend (in terminal 1)
make frontend        # Frontend (in terminal 2)
```

Open [http://localhost:5174](http://localhost:5174) in je browser.

---

## Configuratie

### Stap 1: Docker services (.env.dev)

Open `.env.dev` en vul je LLM API key in:

```bash
ANTHROPIC_API_KEY=sk-ant-...    # Verplicht voor AI features
```

De overige waarden (database wachtwoorden etc.) hebben werkende defaults.

### Stap 2: Backend (klai-portal/backend/.env)

Open `klai-portal/backend/.env` en vul in:

```bash
# Database (wijzig alleen als je een andere poort/wachtwoord gebruikt)
DATABASE_URL=postgresql+asyncpg://klai:klai-dev@localhost:5434/klai

# Zitadel auth (VERPLICHT voor productie-mode)
ZITADEL_PAT=<zie "Zitadel configuratie" hieronder>

# Genereer deze eenmalig (beide zijn 64-char hex strings = 32 bytes):
PORTAL_SECRETS_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
ENCRYPTION_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
SSO_COOKIE_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Dev instellingen
DEBUG=true
MOCK_BILLING=true
FRONTEND_URL=http://localhost:5174
CORS_ORIGINS=http://localhost:5174

# Docker services (match de defaults uit docker-compose.dev.yml)
REDIS_PASSWORD=klai-dev
MONGO_ROOT_PASSWORD=klai-dev
MEILI_MASTER_KEY=klai-dev-meili-key
LITELLM_MASTER_KEY=sk-litellm-dev-key
LITELLM_BASE_URL=http://localhost:4000
```

> **Let op:** `PORTAL_SECRETS_KEY` én `ENCRYPTION_KEY` zijn beide verplicht (beide 64-char hex / 32 bytes). Zonder een van beide crasht de backend met `AES-256 requires a 32-byte key, got 0 bytes`.

### Stap 3: Frontend (klai-portal/frontend/.env.local)

Open `klai-portal/frontend/.env.local` en vul in:

```bash
VITE_OIDC_AUTHORITY=https://auth.getklai.com
VITE_OIDC_CLIENT_ID=362901948573220875
VITE_API_BASE_URL=http://localhost:8010
```

> **Let op:** De `VITE_OIDC_CLIENT_ID` is `362901948573220875` (OIDC Client ID), **niet** `362901948573155339` (dat is de App ID). Deze staan apart in Zitadel.

> **Vite herstart vereist bij env-wijzigingen:** In tegenstelling tot de backend pikt Vite `.env.local` wijzigingen pas op na een volledige herstart (`Ctrl+C` → `npm run dev`). Hot reload werkt niet voor env vars.

---

## Auth Dev Mode (aanbevolen voor lokale dev)

Als je geen Zitadel login flow wil doorlopen bij elke sessie, zet **Auth Dev Mode** aan. Dit bypast OIDC volledig — je bent direct ingelogd zonder browser redirect.

### 1. Voeg je user toe aan de lokale DB

Zoek je Zitadel user ID op via de Zitadel management API (of vraag een teamlid):

```bash
# Jouw Zitadel user ID ophalen (vereist ZITADEL_PAT in .env)
curl -s -H "Authorization: Bearer $ZITADEL_PAT" \
  "https://auth.getklai.com/management/v1/users/_search" \
  -d '{"query":{"limit":20}}' | grep -o '"id":"[^"]*"\|"displayName":"[^"]*"'
```

Zet daarna je user + org in de lokale DB:

```bash
docker exec -i klai-postgres-1 psql -U klai -d klai << 'EOF'
INSERT INTO portal_orgs (zitadel_org_id, name, slug, plan, provisioning_status)
VALUES ('<jouw_zitadel_org_id>', 'Dev Org', 'dev', 'professional', 'complete')
ON CONFLICT (zitadel_org_id) DO NOTHING;

INSERT INTO portal_users (zitadel_user_id, org_id, role, display_name, email, status)
SELECT '<jouw_zitadel_user_id>', id, 'admin', 'Jouw Naam', 'jij@example.com', 'active'
FROM portal_orgs WHERE zitadel_org_id = '<jouw_zitadel_org_id>'
ON CONFLICT (zitadel_user_id) DO NOTHING;
EOF
```

### 2. Activeer Auth Dev Mode

In `klai-portal/backend/.env`:
```bash
AUTH_DEV_MODE=true
AUTH_DEV_USER_ID=<jouw_zitadel_user_id>
```

In `klai-portal/frontend/.env.local`:
```bash
VITE_AUTH_DEV_MODE=true
```

Herstart backend én Vite. Je bent direct ingelogd als de opgegeven user.

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

### Redirect URIs en Dev Mode (al geconfigureerd)

De OIDC app "Klai Portal" (app ID `362901948573155339`) is al geconfigureerd voor lokale development:

- **Redirect URI:** `http://localhost:5174/callback`
- **Post Logout URI:** `http://localhost:5174/logged-out`
- **Allowed Origin:** `http://localhost:5174`
- **Dev Mode:** ingeschakeld (vereist voor `http://` redirect URIs)

> **Zitadel Dev Mode** staat `http://` (zonder TLS) toe als redirect URI. Zonder Dev Mode accepteert Zitadel alleen `https://` URIs. Dev Mode is al ingeschakeld op de Klai Portal app — je hoeft hier niets voor te doen.

**Als Dev Mode per ongeluk is uitgeschakeld:**
1. Ga naar [auth.getklai.com/ui/console](https://auth.getklai.com/ui/console)
2. Projects > **Klai Platform** > Applications > **Klai Portal**
3. Onder **OIDC Configuration**, zet de **Dev Mode** toggle aan
4. Sla op

> **Let op:** De redirect URIs zijn zichtbaar in productie maar vormen geen beveiligingsrisico — Zitadel valideert dat de callback URL overeenkomt met de geregistreerde URIs.

---

## Makefile targets

| Commando | Omschrijving |
|----------|-------------|
| `make help` | Toon alle beschikbare targets |
| `make setup` | Eerste setup: kopieer env files, installeer dependencies |
| `make dev-up` | Start Docker services |
| `make dev-down` | Stop Docker services (data blijft behouden) |
| `make dev-reset` | Stop services EN verwijder alle data (schone start) |
| `make dev-status` | Toon status van Docker services |
| `make dev-logs` | Volg logs van alle Docker services |
| `make backend` | Start FastAPI backend met hot reload (:8010) |
| `make frontend` | Start Vite dev server (:5174) |
| `make migrate` | Draai Alembic database migraties |
| `make lint` | Draai linters (ruff + eslint) |
| `make check` | Draai type checks (pyright + tsc) |

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

### Backend start mislukt: "ZITADEL_PAT required" of "AES-256 requires a 32-byte key"

De `.env` file mist verplichte velden. Controleer:
```bash
grep -E '^(ZITADEL_PAT|DATABASE_URL|PORTAL_SECRETS_KEY|ENCRYPTION_KEY|SSO_COOKIE_KEY)=' klai-portal/backend/.env
```

Alle vijf moeten een waarde hebben. `PORTAL_SECRETS_KEY` en `ENCRYPTION_KEY` zijn beide 64-char hex strings (32 bytes). Genereer met:
```bash
cd klai-portal/backend && uv run python -c "import secrets; print(secrets.token_hex(32))"
```

### Frontend login redirect mislukt: "Errors.App.NotFound"

**Symptoom:** Na klikken op "Inloggen" stuurt Zitadel `{"error":"invalid_request","error_description":"Errors.App.NotFound"}`.

**Oorzaak:** `VITE_OIDC_CLIENT_ID` bevat de App ID in plaats van de OIDC Client ID. Beide zijn 18-cijferige nummers maar zijn **niet** hetzelfde.

**Fix:** Gebruik `362901948573220875` (OIDC Client ID), niet `362901948573155339` (App ID).

Verifieer via Zitadel API:
```bash
curl -s -H "Authorization: Bearer $ZITADEL_PAT" \
  "https://auth.getklai.com/management/v1/projects/362771533686374406/apps/_search" \
  -d '{}' | grep -o '"clientId":"[^"]*"\|"name":"[^"]*"'
```

**Herstart Vite na de wijziging** — env vars worden niet hot-reloaded.

### Frontend login stuurt door naar live app

**Symptoom:** Na Zitadel login kom je uit op `my.getklai.com` in plaats van `localhost:5174`.

**Oorzaak 1:** Vite is niet herstart na `.env.local` wijziging — de oude `client_id` is nog actief.
**Oorzaak 2:** Zitadel gebruikte een bestaande SSO sessie van de live app met diens `redirect_uri`.

**Fix:** Herstart Vite. Of gebruik Auth Dev Mode (zie sectie hierboven) om Zitadel volledig te omzeilen.

### LiteLLM start niet op

LiteLLM wacht tot PostgreSQL healthy is. Check:
```bash
make dev-status     # Zijn alle services healthy?
make dev-logs       # Bekijk LiteLLM logs
```

Als PostgreSQL niet start, controleer of poort 5434 vrij is.

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
# Update VITE_API_BASE_URL in frontend/.env.local mee
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
- Is `VITE_OIDC_CLIENT_ID` correct ingevuld in `klai-portal/frontend/.env.local`?
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
- [klai-portal/frontend/.env.local.example](../../klai-portal/frontend/.env.local.example) — Frontend environment template
- [docker-compose.dev.yml](../../docker-compose.dev.yml) — Docker Compose configuratie
