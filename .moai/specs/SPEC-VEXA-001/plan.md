# SPEC-VEXA-001: Implementatieplan — Vexa Agentic-Runtime Migratie

## Overzicht

Migratie van de huidige geforkte Vexa Lite container (`ghcr.io/getklai/vexa-lite:latest`) naar de nieuwe Vexa agentic-runtime microservice architectuur (`feature/agentic-runtime` branch). Dit is geen image-swap maar een volledige architectuurwissel.

## Gebruikersbeslissingen

| Vraag | Beslissing |
|-------|-----------|
| Teams-ondersteuning | Meteen meenemen |
| Real-time transcriptie | Ja, via Vexa's pipeline met bestaande whisper-server (gpu-01) |
| Interactieve bots (TTS/chat/screen) | Nu niet |
| Zoom-ondersteuning | Niet beschikbaar (confidence 0 in Vexa) |
| Calendar-service | Niet nodig — Klai heeft eigen IMAP listener pipeline |
| Agent-api | Niet nodig — niet gerelateerd aan MoAI |
| MCP server | Niet nodig — niet gerelateerd aan klai-knowledge-mcp |
| Dashboard | Niet nodig — Klai heeft eigen portal |
| Telegram bot | Niet nodig |
| Admin-api | Niet nodig — Klai gebruikt Zitadel |

## Architectuur: Huidig vs Nieuw

### Huidig (wordt vervangen)
```
vexa-bot-manager (single container)
  ├── Vexa Lite all-in-one
  ├── Internal Playwright bot
  ├── Internal transcription
  └── Patches via volume mounts
```

### Nieuw (te deployen)
```
vexa-meeting-api              ← Meeting CRUD, bot lifecycle, transcription collector
vexa-runtime-api              ← Container orchestratie (Docker backend)
vexa-redis                    ← Bot state, pub/sub, transcription streams
vexa-bot (ephemeral)          ← 1 per actieve meeting, ~1.5GB RAM, sterft automatisch
                              ← Gespawnd door runtime-api via Docker socket
```

### Wat we hergebruiken
- **Klai's PostgreSQL** — apart `vexa` database in bestaande cluster
- **Klai's Docker socket proxy** — voor runtime-api container orchestratie
- **Klai's Caddy** — reverse proxy voor Vexa API endpoints
- **Klai's IMAP listener** — calendar invite handling (ongewijzigd)
- **whisper-server (gpu-01)** — bestaande transcriptie backend via SSH tunnel (172.18.0.1:8000), ongewijzigd

## Taakdecompositie

### Fase 1: Infrastructuur (docker-compose + database)

**Task 1.1: Vexa database aanmaken**
- Nieuw database `vexa` in bestaande PostgreSQL cluster
- Init script in `deploy/postgres/` voor automatische creatie
- Vexa's schema wordt door meeting-api zelf gemigreerd (Alembic)

**Task 1.2: docker-compose.yml updaten**
- Verwijder `vexa-bot-manager` service (regels 724-772)
- Voeg toe: `vexa-meeting-api`, `vexa-runtime-api`, `vexa-redis`
- Netwerken: `klai-net` (API access), `socket-proxy` (container orchestratie), `vexa-bots` (bot internet), `net-postgres` (database), `net-redis` (cache)
- Runtime-api heeft toegang nodig tot Docker socket proxy
- Meeting-api environment: `DATABASE_URL`, `REDIS_URL`, `TRANSCRIBER_URL` (→ `http://172.18.0.1:8000/v1/audio/transcriptions`, bestaande whisper-server via SSH tunnel), `ADMIN_API_TOKEN`
- Runtime-api environment: `DOCKER_HOST` (→ socket-proxy), `BOT_IMAGE`, `BOT_NETWORK`
- Resource limits: meeting-api ~256MB, runtime-api ~256MB, vexa-redis ~128MB

**Task 1.3: Caddy routing configureren**
- Interne route voor portal-api → vexa-meeting-api (geen publieke exposure nodig)
- Webhook endpoint route als Vexa webhooks via HTTP callback werken

**Task 1.4: vexa-patches/ verwijderen**
- Verwijder `deploy/vexa-patches/` directory (recording.js, screen-content.js, process.py, schemas.py)
- Verwijder gerelateerde volume mounts uit docker-compose.yml
- Niet meer relevant voor nieuwe architectuur

### Fase 2: Portal Backend Integratie

**Task 2.1: VexaClient herschrijven (`vexa.py`)**
- Nieuwe API endpoints:
  - `POST /bots` → join meeting (platform + meeting_url in body)
  - `DELETE /bots/{platform}/{id}` → stop bot
  - `GET /bots/{platform}/{id}/status` → bot status
  - `GET /transcripts/{platform}/{id}` → get transcripts
- Response format mapping naar Klai's interne structuur
- Auth: `ADMIN_API_TOKEN` header
- Timeout configuratie: bot containers starten kan 30-60s duren

**Task 2.2: meetings.py endpoints updaten**
- `start_meeting()`: nieuwe request/response format
- `stop_meeting()`: nieuwe DELETE endpoint format
- `get_meeting_status()`: nieuwe status velden
- `run_transcription()`: aanpassen voor real-time transcriptie (segments komen van Vexa, niet meer batch via recording)
- `vexa_webhook()`: nieuwe webhook envelope format met signing
- Teams-ondersteuning toevoegen (platform parameter in URL/body)

**Task 2.3: bot_poller.py evalueren**
- Nieuwe Vexa heeft webhooks — bot_poller mogelijk overbodig
- Behoud als fallback voor gemiste webhooks (heartbeat pattern)
- Update polling logic voor nieuwe status API

**Task 2.4: recording_cleanup.py updaten**
- Huidige: Docker exec in vexa-bot-manager container
- Nieuw: Recording API via meeting-api OF directe file deletion als local storage
- GDPR: recordings moeten EU-only blijven, auto-delete na processing

**Task 2.5: knowledge_adapter.py updaten**
- `ingest_vexa_meeting()` aanpassen voor nieuw transcript format
- Speaker labels komen nu per-speaker (betere diarization)
- Segment format kan anders zijn (controleer Vexa's Transcription model)

### Fase 3: Database Migratie

**Task 3.1: VexaMeeting model evalueren**
- Klai-specifieke kolommen behouden: `org_id`, `group_id`, `ical_uid`, `consent_given`, `recording_deleted`
- Nieuwe velden mogelijk nodig: `platform` enum uitbreiden voor Teams
- `bot_id` format kan anders zijn in nieuwe Vexa
- Alembic migratie schrijven voor eventuele schema-wijzigingen

### Fase 4: Configuratie & Security

**Task 4.1: Environment variables**
- Nieuwe vars in `.env.sops`:
  - `VEXA_MEETING_API_URL` (intern, bijv. `http://vexa-meeting-api:8056`)
  - `VEXA_ADMIN_TOKEN` (vervanger van huidige `VEXA_API_KEY`)
  - `VEXA_WEBHOOK_SECRET` (voor webhook signing verificatie)
- Verwijder: `VEXA_BOT_CONTAINER_NAME` (niet meer relevant)
- Update: `vexa_bot_manager_url` → `vexa_meeting_api_url` in config.py

**Task 4.2: Docker socket proxy permissions**
- Runtime-api moet containers kunnen spawnen/stoppen
- Controleer of huidige socket-proxy permissions voldoende zijn
- Vexa-bot containers moeten op `vexa-bots` netwerk (internet access)

**Task 4.3: Multi-tenancy boundary**
- Vexa's admin-api token scoping vs Klai's Zitadel
- Optie A: Enkele admin token voor alle portal-api calls (simpel, huidige aanpak)
- Optie B: Per-org tokens via Vexa's token scoping (complexer, betere isolatie)
- Aanbeveling: Start met Optie A, upgrade later als multi-tenant Vexa nodig is

### Fase 5: Testing & Validatie

**Task 5.1: Lokale test setup**
- docker compose up voor nieuwe Vexa services
- Verify: meeting-api health check, runtime-api Docker access, redis connectivity
- Verify: whisper-server (gpu-01) bereikbaar via SSH tunnel op 172.18.0.1:8000

**Task 5.2: Functionele tests**
- Google Meet bot join + transcriptie (via bestaande whisper-server)
- Teams bot join + transcriptie (nieuw!)
- Bot stop (manueel + webhook)
- Recording download + cleanup
- Calendar invite → auto-join flow (ongewijzigd, maar met nieuwe backend)

**Task 5.3: GDPR compliance check**
- Audio processing EU-only
- Recording auto-delete na transcriptie
- Geen data naar externe services (transcriptie via eigen whisper-server op gpu-01)

## Risico's en Mitigatie

| Risico | Impact | Mitigatie |
|--------|--------|-----------|
| 1.5GB RAM per bot container | Hoog bij veel meetings | Resource limits, max concurrent bots configuratie |
| `feature/agentic-runtime` is geen stable release | Hoog | Pin op specifieke commit hash, niet `:latest` |
| Nieuwe API response formats ongedocumenteerd | Midden | Uitgebreid testen, response logging in VexaClient |
| Docker socket proxy permissions onvoldoende | Midden | Pre-test op dev environment |
| Bot containers op vexa-bots netwerk hebben internet nodig | Laag | Netwerk bestaat al, bevestig routing |

## Referenties

- Huidige VexaClient: `klai-portal/backend/app/services/vexa.py`
- Huidige meetings API: `klai-portal/backend/app/api/meetings.py`
- Huidige bot poller: `klai-portal/backend/app/services/bot_poller.py`
- Huidige recording cleanup: `klai-portal/backend/app/services/recording_cleanup.py`
- Huidige docker-compose: `deploy/docker-compose.yml` (regels 724-772)
- IMAP listener: `klai-portal/backend/app/services/imap_listener.py`
- iCal parser: `klai-portal/backend/app/services/ical_parser.py`
- Invite scheduler: `klai-portal/backend/app/services/invite_scheduler.py`
- Vexa pitfalls: `.claude/rules/klai/pitfalls/vexa-leave-detection.md`
- Research: `.moai/specs/SPEC-VEXA-001/research.md`
- Vexa agentic-runtime: `https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime`

## Geschatte Complexiteit

| Fase | Complexiteit | Bestanden |
|------|-------------|-----------|
| Fase 1: Infrastructuur | Midden | 3-4 bestanden |
| Fase 2: Backend integratie | Hoog | 5-6 bestanden |
| Fase 3: Database migratie | Laag | 1-2 bestanden |
| Fase 4: Config & Security | Laag | 2-3 bestanden |
| Fase 5: Testing | Hoog | Manueel + geautomatiseerd |
