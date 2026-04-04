---
id: SPEC-VEXA-001
version: "1.4"
status: completed
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
| 1.3 | 2026-04-02 | MoAI | **MISLUKTE IMPLEMENTATIEPOGING GEDOCUMENTEERD** — zie sectie 7. Rollback uitgevoerd naar commit vóór 8e04a81. |
| 1.4 | 2026-04-02 | MoAI | **SYNC** — Implementatie voltooid. 2 feat + 15 fix commits. Status → completed. |

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

## 7. Wat er misging — Mislukte Implementatiepoging (2026-04-02)

> **Verplicht lezen vóór implementatie start.** Dit is geen optionele context.

### Wat er fout ging

De eerste implementatiepoging heeft **drie SPEC-constraints tegelijkertijd genegeerd**:

#### Fout 1 — Verkeerde image gebruikt (CRIT)

De implementatie gebruikte `vexaai/vexa-lite:latest` — de **oude monolithische image**.

De SPEC zegt op **twee plaatsen** expliciet het tegenovergestelde:
- Sectie 5: "Image source: Build van `feature/agentic-runtime` branch, **pin op specifieke commit hash**"
- Plan.md risicotabel: "Pin op specifieke commit hash, **niet `:latest`**"

`vexaai/vexa-lite:latest` is de pre-agentic-runtime image. Die heeft een ingebouwde Supervisor die altijd WhisperLive start — ongeacht `TRANSCRIBER_URL`. Dit is niet configureerbaar zonder de image zelf te patchen.

#### Fout 2 — Architectuurwissel niet uitgevoerd (CRIT)

De SPEC zegt letterlijk: "Dit is **geen image-swap maar een volledige architectuurwissel**."

De implementatie deed het wél als een image-swap: één container (`vexa-meeting-api`) in docker-compose in plaats van drie aparte services (`vexa-meeting-api` + `vexa-runtime-api` + ephemere `vexa-bot` containers via Docker socket).

Gevolg: de bot draaide als **subprocess (PID)** inside de container, niet als aparte ephemere Docker container.

#### Fout 3 — Memory limits volstrekt onvoldoende (CRIT)

De SPEC zegt:
- Bot container: ~1.5GB RAM per actieve meeting
- meeting-api: ~256MB, runtime-api: ~256MB

De implementatie zette `deploy.resources.limits.memory: 512M` voor de enkele monolithische container, inclusief WhisperLive én Chromium.

Gevolg: Chromium crashte met `page.evaluate: Target crashed` (OOM kill) elke keer dat de bot probeerde Google Meet te joinen.

#### Fout 4 — WhisperLive draaide op core-01 (CRIT)

`TRANSCRIBER_URL=http://172.18.0.1:8000/...` stuurt transcriptieresultaten naar de externe service, maar **voorkomt niet** dat WhisperLive start. Die start altijd via Supervisor in `vexa-lite`.

De SPEC verbiedt dit impliciet via de architectuurbeschrijving en de Technische Constraints (sectie 5): whisper-server draait op gpu-01, niet op core-01.

#### Fout 5 — Signalen genegeerd (PROC)

Tijdens debugging waren de volgende SPEC-schendingen zichtbaar in de logs en **niet aangekaart**:
- WhisperLive pid 53 zichtbaar in Supervisor → doorgelopen
- Container OOM crash → doorgelopen (oorzaak was SPEC-schending, niet downstream bug)

### Wat wél gedaan is en bewaard blijft

| Werk | Status | Reden |
|------|--------|-------|
| `fix(vexa): use X-API-Key header` (commit `1dd7ea4`) | **Houden** | Nieuwe architectuur gebruikt ook X-API-Key auth |
| `fix(quality): remove unused JSONB import` (commit `300e506`) | **Houden** | Niets met Vexa te maken |
| `vexa` database aangemaakt op core-01 | **Houden** | Nieuwe architectuur gebruikt dezelfde PostgreSQL database |
| `fix(vexa): merge runtime-api into meeting-api` (commit `8e04a81`) | **ROLLEN TERUG** | Bevat de foute monolithische docker-compose setup |
| Handmatige `VEXA_API_KEY` in `/opt/klai/.env` | **Controleren** | Mogelijk anders benoemd in nieuwe architectuur |

### Hoe dit te voorkomen bij de volgende implementatie

**Vóór één regel code schrijven:**

1. Lees sectie 5 (Technische Constraints) en schrijf hier op:
   - Welke exacte image tag/commit hash wordt gebruikt
   - Wat de resource limits zijn per service
   - Welke services NIET mogen draaien op core-01

2. Vraag de gebruiker om bevestiging van deze lijst.

3. Controleer de `feature/agentic-runtime` branch op GitHub en pin de implementatie op een specifieke commit hash — nooit `:latest` of een branch name.

4. Bouw drie aparte services: `vexa-meeting-api`, `vexa-runtime-api`, `vexa-redis`. De bot is een ephemere container, geen subprocess.

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

---

## 8. Implementation Notes (SYNC)

**Phase:** SYNC — 2026-04-02
**Status:** Completed

### Key Commits

| Commit | Type | Description |
|--------|------|-------------|
| `37716d0` | feat | Migrate to agentic-runtime microservice architecture (main implementation) |
| `3ab942c` | feat | Update portal status mapping for agentic-runtime API |
| `1dd7ea4` | fix | Use X-API-Key header with provisioned client token |
| `bf42801` | revert | Revert failed monolithic image attempt (`8e04a81`) |
| `9f81a1d` | fix | Fix Docker network name and meeting-api network membership |
| `f446266` | fix | Use Unix socket for runtime-api Docker backend |
| `23a632d` | fix | Add docker group_add for socket permission on core-01 |
| `064d8cd` | fix | Add vexa DB to postgres init.sql for fresh deployments |
| `5ce12f7` | fix | Retry transcript segments + complete gracefully without recording |
| `dfe420c` | fix | Use GET /bots/status instead of non-existent per-bot status endpoint |
| `db20fd5` | fix | Fix webhook auth, reduce bot timeout, route internally |
| `90e0099` | fix | Token-free webhook auth, joining→recording status, completed meetings clickable |
| `2208466` | fix | Reduce poll_loop complexity below ruff C901 limit |
| `dcc0357` | fix | Add stopping status to badge config and active polling sets |
| `21fddb1` | fix | Normalize completed→done in meeting detail queryFn |
| `45a7c32` | fix | Add vexa-redis password to REDIS_URL connection strings |

**Total:** 2 feat + 1 revert + 1 style + 13 fix commits = 17 vexa-related commits

### What was delivered

- **Three-service architecture:** `vexa-meeting-api`, `vexa-runtime-api`, `vexa-redis` deployed in docker-compose
- **Locally built images:** `vexa-meeting-api:klai` and `vexa-runtime-api:klai` from Vexa `feature/agentic-runtime` branch (commit `600cba04`)
- **VexaClient rewritten:** Bearer auth → X-API-Key, 60s timeout, new /bots endpoints
- **Recording cleanup:** Docker exec → API-based DELETE /recordings/{id}
- **Webhook handler:** Envelope format support, token-free auth on internal Docker network
- **Bot poller:** Updated status mapping (done→completed, processing→stopping)
- **vexa-patches/ removed:** ~4800 lines of fork patches deleted (6 files)
- **EXEC:1 removed** from docker-socket-proxy (no longer needed)
- **profiles.yaml** added for runtime-api bot container templates (shm_size: 1GB for Chromium)
- **Frontend updates:** stopping status badge, completed meetings clickable, queryFn normalization

### What was NOT delivered (deferred)

- **SPEC-VEXA-002:** Transcription-service migration (whisper-server replacement) — deferred
- **WebSocket real-time streaming** to frontend — future feature
- **Zoom support** — not implemented in Vexa

### Failed first attempt (Section 7)

The first implementation attempt (`8e04a81`) used the wrong image (`vexaai/vexa-lite:latest`), wrong architecture (monolith instead of 3 services), and insufficient memory (512MB for Chromium). Reverted via `bf42801`. Full post-mortem in Section 7.

### Decisions

- **Auth:** Single admin token for portal-api → vexa-meeting-api (simplest approach)
- **Webhook auth:** Trust internal Docker network (no HMAC signing) — vexa-meeting-api is the only service that can reach the webhook endpoint
- **Recording:** Disabled (`RECORDING_ENABLED=false`) per GDPR — no persistent audio storage
- **Storage:** `STORAGE_BACKEND=local` — MinIO not deployed
- **TTS:** Out of scope — `TTS_SERVICE_URL` left empty
