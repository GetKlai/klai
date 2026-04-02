---
id: SPEC-VEXA-001
version: "1.1"
status: draft
created: 2026-04-02
updated: 2026-04-02
author: MoAI
priority: high
---

# SPEC-VEXA-001: Vexa Agentic-Runtime Migratie

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|--------|-------|--------|-----------|
| 1.0 | 2026-04-02 | MoAI | Initieel SPEC document |
| 1.1 | 2026-04-02 | MoAI | Scope uitgebreid: Vexa transcription-service vervangt whisper-server als unified transcription backend |

---

## 1. Context

Klai gebruikt momenteel een geforkte Vexa Lite container (`ghcr.io/getklai/vexa-lite:latest`) voor meeting bot functionaliteit. Deze setup heeft fundamentele beperkingen:

- Alleen Google Meet ondersteuning
- Fragiele Playwright DOM scraping voor leave detection (8 gedocumenteerde pitfalls)
- Handmatige patches via volume mounts (Issues #189 en #190)
- Batch transcriptie na meeting-einde (geen real-time)
- Single-container architectuur zonder schaalbaarheidsmogelijkheden

Daarnaast heeft Klai's whisper-server (146 regels code) fundamentele beperkingen als transcriptie backend:

- Globale asyncio.Lock — maximaal 1 concurrent transcriptie request
- Geen prioriteitssysteem (meeting real-time en Scribe uploads concurreren gelijk)
- Geen hallucination detection, geen VAD, geen temperature fallback
- Geen horizontale schaalbaarheid

Vexa heeft een volledige rebuild uitgevoerd op de `feature/agentic-runtime` branch met een modulaire microservice architectuur. Deze SPEC beschrijft de migratie naar deze nieuwe architectuur, inclusief de vervanging van Klai's whisper-server door Vexa's production-grade transcription-service als unified transcription backend voor zowel meetings als Scribe audio uploads.

---

## 2. Requirements (EARS Format)

### 2.1 Ubiquitous Requirements

**[REQ-U-001]** Het systeem SHALL alle meeting audio en transcriptie data exclusief verwerken op EU-infrastructuur (Klai's eigen servers).

**[REQ-U-002]** Het systeem SHALL meeting recordings automatisch verwijderen na voltooiing van transcriptie, conform GDPR data minimalisatie.

**[REQ-U-003]** Het systeem SHALL multi-tenant isolatie handhaven — meetings zijn alleen zichtbaar voor de organisatie die ze heeft gestart.

### 2.2 Event-Driven Requirements

**[REQ-E-001]** WHEN een gebruiker een meeting bot start via de portal, THE SYSTEM SHALL een Vexa bot container spawnen die de meeting joint op het opgegeven platform (Google Meet of Microsoft Teams).

**[REQ-E-002]** WHEN een meeting eindigt (host verlaat, bot wordt gestopt, of meeting timeout), THE SYSTEM SHALL de bot container stoppen en de meeting status updaten naar "completed".

**[REQ-E-003]** WHEN een calendar invite binnenkomt via IMAP met een Google Meet of Teams URL, THE SYSTEM SHALL automatisch een bot inplannen die 60 seconden voor aanvang joint.

**[REQ-E-004]** WHEN Vexa een webhook stuurt met meeting status updates, THE SYSTEM SHALL de VexaMeeting record in de portal database bijwerken.

**[REQ-E-005]** WHEN een meeting actief is, THE SYSTEM SHALL real-time transcriptsegmenten ontvangen van Vexa's transcription pipeline via de vexa-transcription-service.

**[REQ-E-006]** WHEN een gebruiker een meeting stopt, THE SYSTEM SHALL de transcript segmenten consolideren en beschikbaar maken als volledige transcriptie.

**[REQ-E-007]** WHEN een meeting bot audio segmenten verstuurt, THE SYSTEM SHALL deze verwerken met `tier=realtime` (gereserveerde slots) voor prioriteit boven batch-verzoeken.

**[REQ-E-008]** WHEN Scribe (klai-scribe) een audio bestand uploadt voor transcriptie, THE SYSTEM SHALL dit verwerken met `tier=deferred` (best-effort) via dezelfde transcription-service.

**[REQ-E-009]** WHEN de transcription-service overbelast is en een deferred request ontvangt, THE SYSTEM SHALL een 503 met Retry-After header retourneren (fail-fast backpressure).

### 2.3 State-Driven Requirements

**[REQ-S-001]** WHILE een meeting actief is, THE SYSTEM SHALL de bot status monitoren via Vexa's status API en/of webhooks.

**[REQ-S-002]** WHILE de vexa-meeting-api service draait, THE SYSTEM SHALL een health check endpoint aanbieden dat bereikbaar is voor portal-api.

**[REQ-S-003]** WHILE er geen actieve meetings zijn, THE SYSTEM SHALL geen ephemeral bot containers draaien (zero idle resource usage).

**[REQ-S-004]** WHILE de vexa-transcription-service draait, THE SYSTEM SHALL een health check endpoint aanbieden en realtime slots reserveren voor actieve meetings (configureerbaar via `REALTIME_RESERVED_SLOTS`).

### 2.4 Unwanted Behavior Requirements

**[REQ-N-001]** IF de vexa-meeting-api of vexa-runtime-api niet bereikbaar is, THEN THE SYSTEM SHALL een duidelijke foutmelding tonen aan de gebruiker en de meeting status op "error" zetten, NIET de portal laten crashen.

**[REQ-N-002]** IF een bot container crashed of niet kan joinen, THEN THE SYSTEM SHALL de meeting status updaten naar "error" met een beschrijvende error message.

**[REQ-N-003]** IF het maximaal aantal concurrent bot containers is bereikt, THEN THE SYSTEM SHALL nieuwe meeting requests weigeren met een duidelijke foutmelding over capaciteitslimiet.

**[REQ-N-004]** IF de transcription-service niet beschikbaar is, THEN THE SYSTEM SHALL de meeting wel opnemen maar de transcriptie status op "pending" zetten voor latere verwerking.

**[REQ-N-005]** IF de transcription-service een 503 (overbelast) retourneert voor een deferred request, THEN THE SYSTEM SHALL het verzoek bufferen en na de Retry-After periode opnieuw proberen, NIET data verliezen.

### 2.5 Optional Feature Requirements

**[REQ-O-001]** WHERE Microsoft Teams ondersteuning is geconfigureerd, THE SYSTEM SHALL bots kunnen joinen in Teams meetings naast Google Meet.

**[REQ-O-002]** WHERE webhook delivery faalt, THE SYSTEM SHALL een fallback polling mechanisme gebruiken om meeting status bij te werken (behoud bot_poller als backup).

---

## 3. Architectuur

### 3.1 Nieuwe Services

| Service | Image | Functie | Resources |
|---------|-------|---------|-----------|
| `vexa-meeting-api` | Vexa agentic-runtime build | Meeting CRUD, bot lifecycle, transcript collector | ~256MB RAM |
| `vexa-runtime-api` | Vexa agentic-runtime build | Container orchestratie via Docker API | ~256MB RAM |
| `vexa-redis` | `redis:alpine` | Bot state, pub/sub, transcription streams | ~128MB RAM |
| `vexa-bot` (ephemeral) | Vexa bot image | Per-meeting Playwright browser | ~1.5GB RAM per instance |
| `vexa-transcription-service` | Vexa agentic-runtime build | Unified transcription backend (two-tier: realtime/deferred), hallucination detection, VAD, Nginx LB | ~512MB RAM + GPU |

### 3.2 Hergebruikte Klai Services

| Service | Rol in Vexa integratie |
|---------|----------------------|
| PostgreSQL | Aparte `vexa` database voor Vexa's eigen schema |
| Docker socket proxy | Container spawning voor runtime-api |
| Caddy | Interne routing naar meeting-api en transcription-service |
| portal-api | Orchestratie laag, meeting management, tenant scoping |

### 3.3 Verwijderde Componenten

| Component | Reden |
|-----------|-------|
| `vexa-bot-manager` (huidig) | Vervangen door meeting-api + runtime-api |
| `whisper-server` (huidig) | Vervangen door vexa-transcription-service (production-grade, two-tier, 20 concurrent) |
| `deploy/vexa-patches/` | Niet relevant voor nieuwe architectuur |
| Volume mounts voor patches | Niet meer nodig |

### 3.4 Netwerk Topologie

```
portal-api ──(klai-net)──→ vexa-meeting-api ──(klai-net)──→ vexa-runtime-api
    │                           │                                    │
    │                           ├──(net-postgres)──→ PostgreSQL      ├──(socket-proxy)──→ Docker API
    │                           ├──(net-redis)──→ vexa-redis         └──(vexa-bots)──→ vexa-bot containers
    │                           └──(klai-net)──→ vexa-transcription-service
    │                                                    ↑
scribe-api ──(klai-net)──→ vexa-transcription-service    │
                           (tier=deferred)       vexa-bot containers
                                                 (tier=realtime)
```

### 3.5 Data Flow

```
Meeting flow:
1. Gebruiker → portal-api: "Start meeting bot" (platform + URL)
2. portal-api → vexa-meeting-api: POST /bots {platform, meeting_url}
3. vexa-meeting-api → vexa-runtime-api: "Spawn bot container"
4. vexa-runtime-api → Docker: Create + start vexa-bot container
5. vexa-bot → Google Meet/Teams: Join meeting via Playwright
6. vexa-bot → vexa-transcription-service: Stream audio (tier=realtime, prioriteit)
7. vexa-transcription-service → vexa-meeting-api: Transcript segments
8. vexa-meeting-api → portal-api: Webhook met status updates
9. Meeting eindigt → vexa-bot sterft → runtime-api ruimt op
10. portal-api: Consolideer transcriptie, update meeting status

Scribe audio upload flow:
11. scribe-api → vexa-transcription-service: POST /v1/audio/transcriptions (tier=deferred)
12. vexa-transcription-service: Verwerk als er capaciteit is, anders 503 + Retry-After
```

---

## 4. Scope

### In Scope

- Deployment van vexa-meeting-api, vexa-runtime-api, vexa-redis in docker-compose
- Herschrijven VexaClient voor nieuwe API
- Updaten meetings.py endpoints voor nieuw format
- Database migratie voor VexaMeeting model wijzigingen
- Caddy routing configuratie
- Environment variables en secrets management
- Google Meet + Microsoft Teams ondersteuning
- Deployment vexa-transcription-service als unified transcription backend
- Real-time transcriptie via vexa-transcription-service (two-tier: realtime/deferred)
- Scribe audio uploads migreren van whisper-server naar vexa-transcription-service
- Whisper-server met pensioen (verwijderen uit docker-compose)
- GDPR compliance (EU-only, auto-delete recordings)
- Verwijderen van vexa-patches/ en gerelateerde volume mounts

### Buiten Scope

- Vexa agent-api (geen relatie met Klai's systemen)
- Vexa MCP server (geen relatie met klai-knowledge-mcp)
- Vexa dashboard (Klai heeft eigen portal)
- Vexa admin-api user management (Klai gebruikt Zitadel)
- Vexa calendar-service (Klai heeft eigen IMAP listener)
- Vexa Telegram bot
- Interactieve bot features (TTS, chat, screen share)
- Zoom ondersteuning (niet geimplementeerd in Vexa)
- WebSocket real-time streaming naar frontend (toekomstige feature)

---

## 5. Technische Constraints

- **Image source**: Build van `feature/agentic-runtime` branch, pin op specifieke commit hash
- **RAM budget**: Max 3-5 concurrent bots = 4.5-7.5GB extra RAM op core-01
- **Database**: Vexa krijgt eigen database `vexa`, NIET in portal's database
- **Auth**: Enkele admin token voor alle portal-api → vexa-meeting-api calls (simpelste aanpak)
- **Transcriptie**: Vexa transcription-service als `TRANSCRIBER_URL`, vervangt whisper-server. Zelfde OpenAI-compatible API (`POST /v1/audio/transcriptions`). Two-tier: `MAX_CONCURRENT_TRANSCRIPTIONS=20`, `REALTIME_RESERVED_SLOTS=1`, `FAIL_FAST_WHEN_BUSY=true`
- **GPU**: Transcription-service heeft GPU access nodig (zelfde als huidige whisper-server)
- **Netwerk**: Bot containers moeten internet access hebben (vexa-bots netwerk)
- **Socket proxy**: Runtime-api moet containers kunnen create/start/stop/remove

---

## 6. Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| Vexa `feature/agentic-runtime` branch | Extern | Actief in ontwikkeling |
| Vexa transcription-service | Nieuw | Te deployen, vervangt whisper-server |
| Klai scribe-api | Intern | URL swap naar nieuwe transcription-service |
| Docker socket proxy | Intern | Draait al, permissions check nodig |
| PostgreSQL cluster | Intern | Draait al, nieuwe DB aanmaken |
| Redis (dedicated) | Nieuw | Te deployen als `vexa-redis` |
| Caddy | Intern | Route update nodig |
