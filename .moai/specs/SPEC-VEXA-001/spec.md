---
id: SPEC-VEXA-001
version: "1.2"
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
| 1.2 | 2026-04-02 | MoAI | Scope teruggebracht: transcription-service migratie verplaatst naar SPEC-VEXA-002, whisper-server blijft als transcriptie backend |

---

## 1. Context

Klai gebruikt momenteel een geforkte Vexa Lite container (`ghcr.io/getklai/vexa-lite:latest`) voor meeting bot functionaliteit. Deze setup heeft fundamentele beperkingen:

- Alleen Google Meet ondersteuning
- Fragiele Playwright DOM scraping voor leave detection (8 gedocumenteerde pitfalls)
- Handmatige patches via volume mounts (Issues #189 en #190)
- Batch transcriptie na meeting-einde (geen real-time)
- Single-container architectuur zonder schaalbaarheidsmogelijkheden

Vexa heeft een volledige rebuild uitgevoerd op de `feature/agentic-runtime` branch met een modulaire microservice architectuur. Deze SPEC beschrijft de migratie van de meeting bot naar deze nieuwe architectuur. De bestaande whisper-server op gpu-01 blijft ongewijzigd als transcriptie backend (migratie naar Vexa's transcription-service is uitgesteld naar SPEC-VEXA-002).

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

**[REQ-E-005]** WHEN een meeting actief is, THE SYSTEM SHALL real-time transcriptsegmenten ontvangen via Vexa's transcription pipeline en de bestaande whisper-server.

**[REQ-E-006]** WHEN een gebruiker een meeting stopt, THE SYSTEM SHALL de transcript segmenten consolideren en beschikbaar maken als volledige transcriptie.

### 2.3 State-Driven Requirements

**[REQ-S-001]** WHILE een meeting actief is, THE SYSTEM SHALL de bot status monitoren via Vexa's status API en/of webhooks.

**[REQ-S-002]** WHILE de vexa-meeting-api service draait, THE SYSTEM SHALL een health check endpoint aanbieden dat bereikbaar is voor portal-api.

**[REQ-S-003]** WHILE er geen actieve meetings zijn, THE SYSTEM SHALL geen ephemeral bot containers draaien (zero idle resource usage).

### 2.4 Unwanted Behavior Requirements

**[REQ-N-001]** IF de vexa-meeting-api of vexa-runtime-api niet bereikbaar is, THEN THE SYSTEM SHALL een duidelijke foutmelding tonen aan de gebruiker en de meeting status op "error" zetten, NIET de portal laten crashen.

**[REQ-N-002]** IF een bot container crashed of niet kan joinen, THEN THE SYSTEM SHALL de meeting status updaten naar "error" met een beschrijvende error message.

**[REQ-N-003]** IF het maximaal aantal concurrent bot containers is bereikt, THEN THE SYSTEM SHALL nieuwe meeting requests weigeren met een duidelijke foutmelding over capaciteitslimiet.

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

### 3.2 Hergebruikte Klai Services

| Service | Rol in Vexa integratie |
|---------|----------------------|
| PostgreSQL | Aparte `vexa` database voor Vexa's eigen schema |
| Docker socket proxy | Container spawning voor runtime-api |
| Caddy | Interne routing naar meeting-api |
| portal-api | Orchestratie laag, meeting management, tenant scoping |
| whisper-server (gpu-01) | Transcriptie backend via SSH tunnel (172.18.0.1:8000), ongewijzigd |

### 3.3 Verwijderde Componenten

| Component | Reden |
|-----------|-------|
| `vexa-bot-manager` (huidig) | Vervangen door meeting-api + runtime-api |
| `deploy/vexa-patches/` | Niet relevant voor nieuwe architectuur |
| Volume mounts voor patches | Niet meer nodig |

### 3.4 Netwerk Topologie

```
portal-api ──(klai-net)──→ vexa-meeting-api ──(klai-net)──→ vexa-runtime-api
                                │                                    │
                                ├──(net-postgres)──→ PostgreSQL      ├──(socket-proxy)──→ Docker API
                                ├──(net-redis)──→ vexa-redis         └──(vexa-bots)──→ vexa-bot containers
                                │
                                └──(SSH tunnel)──→ whisper-server (gpu-01, 172.18.0.1:8000)
                                                         ↑
                                                  vexa-bot containers (audio)
```

### 3.5 Data Flow

```
Meeting flow:
1. Gebruiker → portal-api: "Start meeting bot" (platform + URL)
2. portal-api → vexa-meeting-api: POST /bots {platform, meeting_url}
3. vexa-meeting-api → vexa-runtime-api: "Spawn bot container"
4. vexa-runtime-api → Docker: Create + start vexa-bot container
5. vexa-bot → Google Meet/Teams: Join meeting via Playwright
6. vexa-bot → whisper-server (gpu-01): Audio voor transcriptie via SSH tunnel
7. whisper-server → vexa-meeting-api: Transcript segments
8. vexa-meeting-api → portal-api: Webhook met status updates
9. Meeting eindigt → vexa-bot sterft → runtime-api ruimt op
10. portal-api: Consolideer transcriptie, update meeting status
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
- GDPR compliance (EU-only, auto-delete recordings)
- Verwijderen van vexa-patches/ en gerelateerde volume mounts

> **Uitgesteld naar SPEC-VEXA-002:** Transcription-service migratie (vervanging whisper-server op gpu-01 door Vexa's transcription-service met CUDA build).

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
- **Transcriptie**: Bestaande whisper-server op gpu-01 als TRANSCRIBER_URL (`http://172.18.0.1:8000/v1/audio/transcriptions`), ongewijzigd
- **Netwerk**: Bot containers moeten internet access hebben (vexa-bots netwerk)
- **Socket proxy**: Runtime-api moet containers kunnen create/start/stop/remove

---

## 6. Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| Vexa `feature/agentic-runtime` branch | Extern | Actief in ontwikkeling |
| whisper-server (gpu-01) | Intern | Draait al, ongewijzigd — bereikbaar via SSH tunnel op 172.18.0.1:8000 |
| Docker socket proxy | Intern | Draait al, permissions check nodig |
| PostgreSQL cluster | Intern | Draait al, nieuwe DB aanmaken |
| Redis (dedicated) | Nieuw | Te deployen als `vexa-redis` |
| Caddy | Intern | Route update nodig |
