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

# Zitadel auth (VERPLICHT)
ZITADEL_PAT=<zie "Zitadel configuratie" hieronder>

# Genereer deze eenmalig:
PORTAL_SECRETS_KEY=<openssl rand -hex 32>
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

### Stap 3: Frontend (klai-portal/frontend/.env.local)

Open `klai-portal/frontend/.env.local` en vul in:

```bash
VITE_OIDC_AUTHORITY=https://auth.getklai.com
VITE_OIDC_CLIENT_ID=<zie "Zitadel configuratie" hieronder>
VITE_API_BASE_URL=http://localhost:8010
```

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

### Backend start mislukt: "ZITADEL_PAT required"

De `.env` file mist verplichte velden. Controleer:
```bash
grep -E '^(ZITADEL_PAT|DATABASE_URL|PORTAL_SECRETS_KEY|SSO_COOKIE_KEY)=' klai-portal/backend/.env
```

Alle vier moeten een waarde hebben.

### Frontend login redirect mislukt

**Symptoom:** Na klikken op "Inloggen" zie je een Zitadel error over ongeldige redirect URI.

**Oorzaak:** De redirect URIs zijn niet toegevoegd aan de Zitadel OIDC app.

**Fix:** Volg de stappen onder "Redirect URIs toevoegen" hierboven.

### LiteLLM start niet op

LiteLLM wacht tot PostgreSQL healthy is. Check:
```bash
make dev-status     # Zijn alle services healthy?
make dev-logs       # Bekijk LiteLLM logs
```

Als PostgreSQL niet start, controleer of poort 5434 vrij is.

### Database migratie mislukt

```bash
# Reset de database volledig
make dev-reset
make dev-up
# Wacht 10 seconden tot PostgreSQL healthy is
make migrate
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
