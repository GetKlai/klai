# SPEC-VEXA-001: Research — Vexa Agentic-Runtime Migratie

## Samenvatting

Vexa heeft een totale rebuild ondergaan op de `feature/agentic-runtime` branch. De huidige Klai-integratie draait een geforkt `ghcr.io/getklai/vexa-lite:latest` image met handmatige patches. De nieuwe Vexa is een modulair platform met 12+ microservices, maar biedt ook een **Vexa Lite** deployment die goed past bij Klai's huidige architectuur.

---

## Huidige Vexa-integratie in Klai

### Docker deployment
- **Image**: `ghcr.io/getklai/vexa-lite:latest` (geforkt, met patches)
- **Service**: `vexa-bot-manager` in `deploy/docker-compose.yml` (regels 724-772)
- **Resources**: 1 CPU, 1GB RAM
- **Networks**: klai-net, socket-proxy, vexa-bots, net-postgres, net-redis
- **Patches gemount** (volume overrides):
  - `./vexa-patches/screen-content.js` → `/app/vexa-bot/dist/services/screen-content.js` (Issue #189)
  - `./vexa-patches/recording.js` → `/app/vexa-bot/dist/platforms/googlemeet/recording.js` (Issue #190)

### Portal backend-integratie
- **VexaClient** (`klai-portal/backend/app/services/vexa.py`, 143 regels): HTTP client met 6 methoden (start_bot, stop_bot, get_meeting, get_status, get_recording, get_transcript_segments)
- **Meetings API** (`klai-portal/backend/app/api/meetings.py`, 647 regels): 8 endpoints onder `/api/bots/`
- **Bot Poller** (`klai-portal/backend/app/services/bot_poller.py`, 128 regels): elke 10s pollen voor naturally-ended meetings
- **Recording Cleanup** (`klai-portal/backend/app/services/recording_cleanup.py`, 172 regels): GDPR-compliant verwijdering via Docker exec
- **Knowledge Adapter** (`klai-portal/backend/app/services/knowledge_adapter.py`): transcripties naar KB ingest pipeline

### Database model
- **VexaMeeting** (`klai-portal/backend/app/models/meetings.py`, 48 regels): 24 kolommen
- 6 Alembic migraties voor de meetings tabel

### Bekende problemen (pitfalls)
- 8 gedocumenteerde pitfalls in `vexa-leave-detection.md`
- Playwright DOM scraping is fundamenteel fragiel (Google Meet UI updates breken selectors)
- Alleen `page.on('crash')` handler werkt betrouwbaar voor meeting-end detectie
- `everyoneLeftTimeout` veldnaam-mismatch met upstream
- Video-blocking regressie bij lokaal bouwen (Issue #189)
- Fake participant counting (Issue #190) — gepatcht maar fragiel

---

## Nieuwe Vexa agentic-runtime

### Architectuur
De nieuwe Vexa is een modulair platform met 5 core API services:

| Service | Functie |
|---------|---------|
| **api-gateway** | Reverse proxy, rate limiting, API key validatie |
| **admin-api** | User/org CRUD, scoped API tokens |
| **meeting-api** | Meeting CRUD, bot lifecycle, recordings, voice agent controls |
| **agent-api** | Agent sessions, Claude CLI, workspace sync |
| **runtime-api** | Container orchestratie (Docker/K8s backends) |

Plus meeting bot (TypeScript/Playwright), transcription service (faster-whisper), TTS service, MCP server, dashboard (Next.js), en Telegram bot.

### Deployment opties
1. **Hosted** (vexa.ai cloud)
2. **Vexa Lite** (single container, extern Postgres + transcriptie) ← **relevant voor Klai**
3. **Docker Compose** (full stack, 12 services)
4. **Helm** (Kubernetes)

### Vexa Lite deployment
```bash
docker run -d \
  --name vexa \
  -p 8056:8056 \
  -e DATABASE_URL="postgresql://user:pass@host/vexa" \
  -e ADMIN_API_TOKEN="your-admin-token" \
  -e TRANSCRIBER_URL="https://transcription.service" \
  -e TRANSCRIBER_API_KEY="transcriber-token" \
  vexaai/vexa-lite:latest
```

### Nieuwe features relevant voor Klai
1. **Multi-platform**: Google Meet + Microsoft Teams + Zoom (vs alleen Google Meet nu)
2. **Per-speaker audio pipeline**: LocalAgreement-2 algoritme, hallucination filtering, speaker streams
3. **Interactieve bots**: speak (TTS), chat, screen share, avatar
4. **MCP server**: 17 tools voor AI agents
5. **Betere API**: consistentere endpoints, WebSocket streaming
6. **Recordings**: S3/MinIO/local storage met lifecycle management
7. **Webhooks**: gestandaardiseerd envelope formaat, signing
8. **Token scoping**: bot/tx/admin scopes, 90% confidence

### API endpoints (nieuw)
- `POST /bots` — join meeting (zelfde als nu)
- `DELETE /bots/{platform}/{id}` — stop bot (zelfde als nu)
- `GET /bots/{platform}/{id}/status` — bot status
- `GET /transcripts/{platform}/{id}` — get transcripts (zelfde als nu)
- `POST /bots/{platform}/{id}/speak` — TTS in meeting (**nieuw**)
- `POST /bots/{platform}/{id}/chat` — chat in meeting (**nieuw**)
- `POST /bots/{platform}/{id}/screen` — screen share (**nieuw**)
- `GET /recordings/...` — recording management (**verbeterd**)
- `WS /ws/{platform}/{id}` — real-time transcript streaming (**nieuw**)

### Confidence scores (feature readiness)
| Feature | Score | Status |
|---------|-------|--------|
| Realtime transcription (GMeet/Teams) | 90 | Production-ready |
| Multi-platform (GMeet) | 75 | Goed, edge cases |
| Multi-platform (Teams) | 65 | Werkt, admissie edge cases |
| Multi-platform (Zoom) | 0 | Niet geimplementeerd |
| MCP integration | 90 | Production-ready |
| Post-meeting transcription | 85 | Pipeline werkt |
| Webhooks | 85 | Gestandaardiseerd |
| Token scoping | 90 | 14/14 tests passen |
| Speaking bot | 0 | Niet E2E getest |
| Chat | 0 | Niet E2E getest |

### Database schema (nieuw)
- `Meeting`: user_id, platform, platform_specific_id, status, bot_container_id, data (JSONB)
- `Transcription`: meeting_id, text, speaker, language, segment_id
- `MeetingSession`: meeting_id, session_uid
- `Recording`: meeting_id, source, status
- `MediaFile`: recording_id, type, format, storage_path, storage_backend
- `CalendarEvent`: user_id, external_event_id, meeting_url, platform

---

## Klai-specifieke overwegingen

### Wat we nodig hebben
1. **Meeting bot** voor Google Meet (primair) + Teams (secundair)
2. **Post-meeting batch transcriptie** via onze eigen Whisper server
3. **Speaker diarization** (per-speaker labels)
4. **Webhook** op meeting completion
5. **GDPR compliance**: EU-only audio, auto-delete recordings
6. **Multi-tenant scoping** via portal-api (niet Vexa's eigen user systeem)

### Wat we NIET nodig hebben
- Agent runtime (we hebben MoAI)
- Dashboard (we hebben klai-portal)
- Telegram bot
- MCP server (we hebben klai-knowledge-mcp)
- TTS / speaking bot / chat / screen share (voorlopig niet)
- Vexa's eigen user/token management (we gebruiken Zitadel)

### Migratierisico's
1. **API-incompatibiliteit**: nieuwe endpoints/response formats vereisen VexaClient update
2. **Database schema**: nieuw schema, Klai-specifieke kolommen (org_id, group_id, ical_uid) moeten behouden worden
3. **Patches**: mogelijk niet meer nodig als upstream bugs gefixt zijn
4. **Recording cleanup**: nieuwe recording storage (S3/local) vs huidige Docker exec cleanup
5. **Bot poller**: webhook-gedrag kan anders zijn
6. **Multi-tenancy**: Vexa's admin-api vs Klai's Zitadel — hoe combineren?
7. **Whisper integratie**: Vexa's transcription service vs Klai's whisper-server

### Aanbevolen aanpak: Vexa Lite
**Gebruik Vexa Lite** (single container) in plaats van de full stack:
- Past bij Klai's huidige deployment (1 container voor vexa-bot-manager)
- Externe transcriptie via Klai's eigen Whisper server
- Minimale extra services nodig
- Upgrade van `ghcr.io/getklai/vexa-lite:latest` (onze fork) naar `vexaai/vexa-lite:latest` (officieel)

### Referentie-implementaties
- Huidige VexaClient: `klai-portal/backend/app/services/vexa.py`
- Huidige meetings API: `klai-portal/backend/app/api/meetings.py`
- Vexa Lite API: poort 8056, zelfde `/bots` en `/transcripts` endpoints

---

## Open vragen voor SPEC

1. **Teams-ondersteuning**: willen we Teams-ondersteuning meteen meenemen of later?
2. **Zoom-ondersteuning**: relevantie voor Klai-klanten?
3. **Real-time transcriptie**: huidige aanpak is post-meeting batch — willen we real-time?
4. **Interactieve bots**: is TTS/chat/screen share relevant voor de roadmap?
5. **Recording storage**: S3/MinIO of lokaal? Impact op GDPR compliance.
6. **Vexa's database**: eigen Postgres DB behouden of data in Klai's portal DB consolideren?
7. **Patches**: zijn upstream issues #189 en #190 gefixt in de nieuwe versie?
