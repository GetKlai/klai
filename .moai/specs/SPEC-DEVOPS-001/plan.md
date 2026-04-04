---
id: SPEC-DEVOPS-001
type: plan
version: 1.0.0
status: completed
created: 2026-03-26
updated: 2026-04-03
---

# SPEC-DEVOPS-001: Implementatieplan

## Overzicht

Dit plan beschrijft de gefaseerde implementatie van de observability stack. De fasen zijn geordend op afhankelijkheden: eerst de infrastructuur (Alloy config, Grafana provisioning), dan applicatiewijzigingen (structlog), vervolgens cross-server shipping, en tot slot code-kwaliteitsregels.

---

## Phase 1: Alloy Config + Grafana Provisioning (Repository)

**Prioriteit:** Primair doel
**Scope:** Configuratiebestanden committen, geen server-wijzigingen

### Taken

1. **`deploy/alloy/config.alloy`** schrijven
   - `loki.source.docker` component met Docker socket discovery
   - `loki.process` stage: JSON-parsing voor `level`-extractie
   - Label-toevoeging: `service` (uit `com.docker.compose.service`), `server=core-01`, `env=production`
   - `loki.write` output naar `http://victorialogs:9428/insert/jsonline`

2. **`deploy/grafana/provisioning/datasources/victorialogs.yaml`** schrijven
   - Datasource naam: `VictoriaLogs`
   - Type: `victoriametrics-logs-datasource` (of `loki` als fallback)
   - URL: `http://victorialogs:9428`
   - Access: `proxy`

3. **`deploy/grafana/provisioning/dashboards/dashboards.yaml`** schrijven
   - Provider-configuratie die naar de dashboards-directory verwijst
   - `disableDeletion: true` om provisioned dashboards te beschermen

4. **`deploy/grafana/provisioning/dashboards/logs.json`** schrijven
   - Log Explorer dashboard met variabelen voor `service`, `server`, `level`
   - Log-tabel panel met tijdstempel, service, level, event
   - Histogram panel: logs per service over tijd

### Tech stack

- Grafana Alloy config syntax (HCL-achtig)
- Grafana provisioning YAML
- Grafana dashboard JSON model

### Verificatie

- Config-bestanden doorlopen linting/validatie
- Alloy config bevat alle vereiste components
- Grafana provisioning volgt het officieel schema

---

## Phase 2: structlog in Python Services

**Prioriteit:** Primair doel
**Scope:** Alle 8 Python-services

### Taken per service

Voor elke service uit de inventory:

1. **Dependency toevoegen**
   - `structlog` toevoegen aan `pyproject.toml` dependencies

2. **Logging setup module aanmaken**
   - Bestand: `app/logging_setup.py` (of `logging_setup.py` afhankelijk van projectstructuur)
   - Functie: `setup_logging(service_name: str, log_format: str = "json")`
   - Configureert structlog processor chain
   - Leest `LOG_FORMAT` environment variable (default: `json`)
   - Bindt `service` context variable

3. **Setup aanroepen in entrypoint**
   - `setup_logging()` aanroepen in `main.py` voor app-creatie
   - `SERVICE_NAME` meegeven uit environment variable of hardcoded fallback

4. **Logging context middleware (alleen portal-api)**
   - FastAPI middleware toevoegen die bij elke request `structlog.contextvars.clear_contextvars()` aanroept en daarna `bind_contextvars(org_id=..., user_id=..., request_id=...)` bindt
   - `request_id` genereren met `uuid4()` als header `X-Request-ID` ontbreekt
   - `org_id` en `user_id` ophalen uit de geverifieerde JWT/session context (beschikbaar via bestaande auth dependency)

4. **`logging.basicConfig()` verwijderen**
   - Alle `logging.basicConfig()`-aanroepen vervangen door de setup-functie

5. **Logger-naamgeving standaardiseren**
   - `log = logging.getLogger(...)` hernoemen naar `logger = logging.getLogger(__name__)`

### Service-specifieke aandachtspunten

| Service              | Pad                            | Aandachtspunt                                    |
|----------------------|--------------------------------|--------------------------------------------------|
| `portal-api`         | `klai-portal/backend/`              | Grootste codebase + **logging context middleware toevoegen** (org_id, user_id, request_id via `structlog.contextvars`) |
| `klai-connector`     | `deploy/klai-connector/`       | Mogelijk weinig eigen logging                     |
| `klai-mailer`        | `deploy/klai-mailer/`          | E-mail-gerelateerde context in logs               |
| `klai-knowledge-mcp` | `deploy/klai-knowledge-mcp/`   | MCP-specifieke context                            |
| `scribe-api`         | `klai-scribe/scribe-api/`           | Audio-processing context                          |
| `whisper-server`     | `klai-scribe/whisper-server/`       | GPU/model-gerelateerde context                    |
| `research-api`       | `klai-focus/research-api/`          | Research-pipeline context                         |
| `retrieval-api`      | `klai-retrieval-api/`               | Vector search context                             |

### Tech stack

- `structlog` (laatste stabiele versie)
- Python `logging` stdlib
- `structlog.stdlib` bridge

### Verificatie

- `docker logs <service> | tail -1 | python -m json.tool` slaagt voor elke service
- `LOG_FORMAT=console` produceert leesbare tekst
- Geen `logging.basicConfig()` meer in de codebase

---

## Phase 3: public-01 Alloy + Caddy Ingest Route

**Prioriteit:** Secundair doel
**Scope:** Server-configuratie core-01 en public-01

### Taken

1. **Caddy route toevoegen op core-01**
   - Route voor `logs-ingest.${DOMAIN}` in Caddyfile
   - Reverse proxy naar `victorialogs:9428`
   - Bearer token-validatie via `header` matcher
   - `VICTORIALOGS_INGEST_TOKEN` environment variable

2. **DNS record aanmaken**
   - A-record of CNAME voor `logs-ingest.${DOMAIN}` naar core-01

3. **Alloy deployment documenteren voor public-01**
   - Configuratiebestand voor public-01 Alloy (vergelijkbaar met core-01, maar met `server=public-01` label)
   - Output: HTTPS naar `logs-ingest.${DOMAIN}/insert/jsonline` met bearer token
   - Deployment via Coolify of standalone Docker container

4. **Environment variabelen**
   - `VICTORIALOGS_INGEST_TOKEN` genereren met `openssl rand -base64 32`
   - Token documenteren in `.env.example` of deploy-documentatie
   - Token configureren in zowel Caddy (core-01) als Alloy (public-01)

### Tech stack

- Caddy reverse proxy configuratie
- Grafana Alloy config
- DNS management

### Verificatie

- `curl -H "Authorization: Bearer $TOKEN" https://logs-ingest.${DOMAIN}/health` retourneert 200
- `curl https://logs-ingest.${DOMAIN}/insert/jsonline` zonder token retourneert 401
- Logs van public-01 verschijnen in Grafana met `server=public-01` label

---

## Phase 4: Ruff Logging Rules

**Prioriteit:** Secundair doel
**Scope:** Alle `pyproject.toml`-bestanden met ruff-configuratie

### Taken

1. **Ruff rules toevoegen**
   - `G` (flake8-logging-format) toevoegen aan `select` in alle `pyproject.toml`
   - `LOG` (flake8-logging) toevoegen aan `select` in alle `pyproject.toml`

2. **Violations fixen**
   - Alle bestaande violations oplossen
   - Eventuele `# noqa`-commentaren alleen met expliciete reden

### Tech stack

- ruff linter
- `pyproject.toml` configuratie

### Verificatie

- `ruff check .` geeft geen `G`- of `LOG`-violations
- Geen `log =` variabelen meer in de codebase (alleen `logger =`)

---

## Risico-analyse

| Risico | Impact | Kans | Mitigatie |
|--------|--------|------|-----------|
| Alloy config bestaat al op server maar niet in repo | Bestaande logs gaan verloren bij overschrijven | Medium | **Check bestaande config op server voor deploy**: `docker exec alloy cat /etc/alloy/config.alloy` |
| VictoriaLogs plugin niet beschikbaar in Grafana | Dashboard werkt niet | Laag | Fallback naar Loki-compatibele datasource |
| structlog breekt bestaande log-parsing | Monitoring-gaps tijdens migratie | Medium | Per service uitrollen, niet allemaal tegelijk |
| public-01 Alloy kan core-01 niet bereiken | Logs van public-01 komen niet aan | Laag | DNS en firewall testen voor deployment |
| Bearer token lekt via logs | Beveiligingsrisico | Laag | Token genereren met `openssl rand`, nooit in code committen |
| Docker socket-toegang voor Alloy | Security surface | Laag | Read-only mount: `/var/run/docker.sock:/var/run/docker.sock:ro` |

---

## Afhankelijkheden tussen fasen

```
Phase 1 (Alloy config + Grafana provisioning)
    |
    v
Phase 2 (structlog in Python services) -- kan parallel met Phase 1
    |
    v
Phase 3 (public-01 Alloy) -- vereist werkende Phase 1
    |
    v
Phase 4 (ruff regels) -- vereist voltooide Phase 2
```

Phase 1 en Phase 2 kunnen parallel uitgevoerd worden. Phase 3 vereist dat de Alloy-configuratie (Phase 1) correct werkt. Phase 4 is een code-kwaliteitsverbetering die afhankelijk is van de voltooide structlog-migratie (Phase 2).

---

## Expert Consultatie

Dit SPEC bevat significante infrastructuur- en backend-componenten:

- **expert-backend**: Aanbevolen voor structlog setup-patronen, processor chain design, en integratie met bestaande Python-services
- **expert-devops**: Aanbevolen voor Alloy-configuratie, Caddy reverse proxy setup, en Docker networking tussen core-01 en public-01
