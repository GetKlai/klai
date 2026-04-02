# SPEC-VEXA-001: Implementatieplan — Vexa Agentic-Runtime Migratie

## Overzicht

Migratie van de huidige geforkte Vexa Lite container (`ghcr.io/getklai/vexa-lite:latest`) naar de nieuwe Vexa agentic-runtime microservice architectuur (`feature/agentic-runtime` branch). Dit is geen image-swap maar een volledige architectuurwissel.

## Gebruikersbeslissingen

| Vraag | Beslissing |
|-------|-----------|
| Teams-ondersteuning | Meteen meenemen |
| Real-time transcriptie | Ja, via Vexa's transcription-service (vervangt whisper-server) |
| Transcription-service als unified backend | Ja — vervangt whisper-server voor meetings EN Scribe audio |
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
vexa-transcription-service    ← Unified transcription backend (vervangt whisper-server)
                              ← Two-tier: realtime (meetings) + deferred (Scribe uploads)
                              ← 20 concurrent, hallucination detection, VAD, Nginx LB
vexa-bot (ephemeral)          ← 1 per actieve meeting, ~1.5GB RAM, sterft automatisch
                              ← Gespawnd door runtime-api via Docker socket
```

### Wat we hergebruiken
- **Klai's PostgreSQL** — apart `vexa` database in bestaande cluster
- **Klai's Docker socket proxy** — voor runtime-api container orchestratie
- **Klai's Caddy** — reverse proxy voor Vexa API endpoints
- **Klai's IMAP listener** — calendar invite handling (ongewijzigd)

### Wat met pensioen gaat
- **whisper-server** — vervangen door vexa-transcription-service (zelfde API: `POST /v1/audio/transcriptions`, maar 20x concurrent, two-tier priority, hallucination detection)

## Taakdecompositie

### Fase 1: Infrastructuur (docker-compose + database)

**Task 1.1: Vexa database aanmaken**
- Nieuw database `vexa` in bestaande PostgreSQL cluster
- Init script in `deploy/postgres/` voor automatische creatie
- Vexa's schema wordt door meeting-api zelf gemigreerd (Alembic)

**Task 1.2: docker-compose.yml updaten**
- Verwijder `vexa-bot-manager` service (regels 724-772)
- Verwijder `whisper-server` service (wordt vervangen door vexa-transcription-service)
- Voeg toe: `vexa-meeting-api`, `vexa-runtime-api`, `vexa-redis`, `vexa-transcription-service`
- Netwerken: `klai-net` (API access), `socket-proxy` (container orchestratie), `vexa-bots` (bot internet), `net-postgres` (database), `net-redis` (cache)
- Runtime-api heeft toegang nodig tot Docker socket proxy
- Meeting-api environment: `DATABASE_URL`, `REDIS_URL`, `TRANSCRIBER_URL` (→ vexa-transcription-service), `ADMIN_API_TOKEN`
- Runtime-api environment: `DOCKER_HOST` (→ socket-proxy), `BOT_IMAGE`, `BOT_NETWORK`
- Transcription-service environment: `MAX_CONCURRENT_TRANSCRIPTIONS=20`, `REALTIME_RESERVED_SLOTS=1`, `FAIL_FAST_WHEN_BUSY=true`, `MODEL_NAME=large-v3-turbo`, `ENABLE_VAD=true`
- Transcription-service: GPU access (deploy.resources.reservations.devices), zelfde GPU als huidige whisper-server
- Resource limits: meeting-api ~256MB, runtime-api ~256MB, vexa-redis ~128MB, transcription-service ~512MB + GPU

**Task 1.3: Caddy routing configureren**
- Interne route voor portal-api → vexa-meeting-api (geen publieke exposure nodig)
- Webhook endpoint route als Vexa webhooks via HTTP callback werken

**Task 1.4: vexa-patches/ en whisper-server verwijderen**
- Verwijder `deploy/vexa-patches/` directory (recording.js, screen-content.js, process.py, schemas.py)
- Verwijder whisper-server service uit docker-compose.yml
- Verwijder whisper-server gerelateerde configuratie (Caddy routes, env vars)
- Niet meer relevant voor nieuwe architectuur

**Task 1.5: vexa-transcription-service deployen**
- Vexa transcription-service uit `feature/agentic-runtime` branch
- Zelfde `POST /v1/audio/transcriptions` endpoint (OpenAI-compatible, drop-in vervanging)
- Two-tier admission control: `realtime` (gereserveerde slots) + `deferred` (best-effort)
- Hallucination detection: compression_ratio > 1.8, avg_logprob < -1.0
- Silero VAD met per-request overrides
- Temperature fallback chain: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
- GPU access nodig (zelfde GPU als huidige whisper-server)
- Nginx load balancer configuratie (least-connections)
- faster-whisper large-v3-turbo model (zelfde als huidige whisper-server)

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

**Task 2.6: scribe-api WhisperHttpProvider URL swap**
- `klai-scribe/scribe-api/app/services/providers.py` — `WhisperHttpProvider`
- Wijzig `settings.whisper_server_url` → URL van vexa-transcription-service
- Voeg `tier=deferred` parameter toe aan requests (Scribe uploads zijn niet real-time)
- Test: audio upload via scribe-api → transcriptie via vexa-transcription-service
- SpeechProvider abstractie maakt dit triviaal (alleen URL + tier parameter)

**Task 2.7: meetings.py whisper-server fallback verwijderen**
- `run_transcription()` in meetings.py heeft een fallback pad dat direct naar whisper-server gaat
- Verwijder whisper-server directe aanroep, vervang door vexa-transcription-service
- Fallback pad: meeting-api segmenten → als niet beschikbaar → transcription-service met tier=deferred

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
  - `VEXA_TRANSCRIPTION_URL` (intern, bijv. `http://vexa-transcription-service:8069`)
- Verwijder: `VEXA_BOT_CONTAINER_NAME` (niet meer relevant)
- Verwijder: `WHISPER_SERVER_URL` (whisper-server wordt vervangen)
- Update: `vexa_bot_manager_url` → `vexa_meeting_api_url` in config.py
- Update: scribe-api `WHISPER_SERVER_URL` → `VEXA_TRANSCRIPTION_URL`

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
- Verify: transcription-service health check, GPU access, model loaded

**Task 5.2: Functionele tests**
- Google Meet bot join + transcriptie (via transcription-service, tier=realtime)
- Teams bot join + transcriptie (nieuw!)
- Bot stop (manueel + webhook)
- Recording download + cleanup
- Calendar invite → auto-join flow (ongewijzigd, maar met nieuwe backend)
- Scribe audio upload → transcriptie via transcription-service (tier=deferred)
- Concurrent meeting + Scribe upload → verify prioriteit (realtime krijgt voorrang)

**Task 5.3: GDPR compliance check**
- Audio processing EU-only
- Recording auto-delete na transcriptie
- Geen data naar externe services (transcriptie via eigen vexa-transcription-service)

**Task 5.4: Whisper-server verwijdering validatie**
- Verify: geen services meer die whisper-server URL refereren
- Verify: scribe-api werkt met nieuwe transcription-service URL
- Verify: meetings.py fallback pad werkt met transcription-service
- Verify: whisper-server container draait niet meer

## Risico's en Mitigatie

| Risico | Impact | Mitigatie |
|--------|--------|-----------|
| 1.5GB RAM per bot container | Hoog bij veel meetings | Resource limits, max concurrent bots configuratie |
| `feature/agentic-runtime` is geen stable release | Hoog | Pin op specifieke commit hash, niet `:latest` |
| Nieuwe API response formats ongedocumenteerd | Midden | Uitgebreid testen, response logging in VexaClient |
| Docker socket proxy permissions onvoldoende | Midden | Pre-test op dev environment |
| Transcription-service GPU compatibility | Midden | Zelfde GPU als whisper-server, test model loading |
| Scribe-api compatibility met transcription-service | Laag | Zelfde API interface, alleen URL + tier parameter wijziging |
| Bot containers op vexa-bots netwerk hebben internet nodig | Laag | Netwerk bestaat al, bevestig routing |

## Referenties

- Huidige VexaClient: `klai-portal/backend/app/services/vexa.py`
- Huidige meetings API: `klai-portal/backend/app/api/meetings.py`
- Huidige bot poller: `klai-portal/backend/app/services/bot_poller.py`
- Huidige recording cleanup: `klai-portal/backend/app/services/recording_cleanup.py`
- Huidige docker-compose: `deploy/docker-compose.yml` (regels 724-772)
- Huidige whisper-server: `klai-scribe/whisper-server/main.py` (146 regels, wordt vervangen)
- Scribe providers: `klai-scribe/scribe-api/app/services/providers.py` (WhisperHttpProvider)
- IMAP listener: `klai-portal/backend/app/services/imap_listener.py`
- iCal parser: `klai-portal/backend/app/services/ical_parser.py`
- Invite scheduler: `klai-portal/backend/app/services/invite_scheduler.py`
- Vexa pitfalls: `.claude/rules/klai/pitfalls/vexa-leave-detection.md`
- Research: `.moai/specs/SPEC-VEXA-001/research.md`
- Vexa agentic-runtime: `https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime`
- Vexa transcription-service: `https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime/packages/transcription-service`

## Geschatte Complexiteit

| Fase | Complexiteit | Bestanden |
|------|-------------|-----------|
| Fase 1: Infrastructuur | Midden-Hoog | 4-6 bestanden (incl. transcription-service + whisper-server verwijdering) |
| Fase 2: Backend integratie | Hoog | 7-8 bestanden (incl. scribe-api provider swap) |
| Fase 3: Database migratie | Laag | 1-2 bestanden |
| Fase 4: Config & Security | Laag | 2-3 bestanden |
| Fase 5: Testing | Hoog | Manueel + geautomatiseerd |
