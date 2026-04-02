# SPEC-VEXA-001: Acceptance Criteria — Vexa Agentic-Runtime Migratie

## Scenario 1: Google Meet Bot Join en Transcriptie

**Given** de Vexa microservices (meeting-api, runtime-api, vexa-redis) draaien op core-01
**And** de vexa-transcription-service is bereikbaar op het interne netwerk
**And** een gebruiker is ingelogd in de Klai portal met een geldige organisatie

**When** de gebruiker een Google Meet bot start via de portal met een geldige meeting URL

**Then** portal-api stuurt een POST /bots request naar vexa-meeting-api
**And** vexa-runtime-api spawnt een ephemeral vexa-bot container
**And** de bot joint de Google Meet als browser participant
**And** de meeting status in de portal database wordt "in_progress"
**And** transcript segmenten worden real-time verwerkt via de vexa-transcription-service (tier=realtime)
**And** na meeting-einde wordt de bot container automatisch gestopt
**And** de meeting status wordt "completed" met volledige transcriptie beschikbaar

## Scenario 2: Microsoft Teams Bot Join

**Given** de Vexa microservices draaien en Teams ondersteuning is actief
**And** een gebruiker heeft een geldige Teams meeting URL

**When** de gebruiker een Teams meeting bot start via de portal

**Then** portal-api stuurt een POST /bots request met platform "teams"
**And** de bot joint de Teams meeting
**And** transcriptie verloopt via dezelfde pipeline als Google Meet
**And** de meeting wordt correct opgeslagen met platform="teams"

## Scenario 3: Bot Stop door Gebruiker

**Given** een meeting bot is actief (status "in_progress")

**When** de gebruiker de bot stopt via de portal

**Then** portal-api stuurt een DELETE /bots/{platform}/{id} request
**And** de bot container wordt gestopt en verwijderd
**And** de meeting status wordt "completed"
**And** beschikbare transcript segmenten worden bewaard

## Scenario 4: Meeting Einde via Webhook

**Given** een meeting bot is actief
**And** de webhook endpoint is geconfigureerd in vexa-meeting-api

**When** de meeting eindigt (host verlaat of alle deelnemers weg)

**Then** Vexa stuurt een webhook naar portal-api met de nieuwe status
**And** portal-api valideert de webhook signature
**And** de meeting status wordt bijgewerkt naar "completed"
**And** de bot container wordt automatisch opgeruimd door runtime-api

## Scenario 5: Calendar Invite Auto-Join

**Given** de IMAP listener draait en pollt voor nieuwe invites
**And** een Google Meet of Teams invite wordt gestuurd naar meet@getklai.com

**When** de invite wordt ontvangen en geparsed

**Then** het systeem plant een bot join in op DTSTART - 60 seconden
**And** op het geplande tijdstip wordt een meeting aangemaakt via de NIEUWE VexaClient
**And** de bot joint automatisch via het nieuwe Vexa systeem

## Scenario 6: Foutafhandeling — Service Onbeschikbaar

**Given** vexa-meeting-api is niet bereikbaar (service down)

**When** een gebruiker een meeting bot probeert te starten

**Then** portal-api retourneert een duidelijke foutmelding
**And** de meeting status wordt "error" met beschrijvende error_message
**And** portal-api crasht NIET

## Scenario 7: Foutafhandeling — Bot Container Crash

**Given** een bot container is gestart maar crasht tijdens de meeting

**When** runtime-api detecteert dat de container gestopt is

**Then** de meeting status wordt bijgewerkt via webhook of polling
**And** de error wordt gelogd met container details
**And** eventueel verzamelde transcript segmenten blijven bewaard

## Scenario 8: GDPR Compliance — Recording Cleanup

**Given** een meeting is voltooid met audio recording

**When** de transcriptie succesvol is afgerond

**Then** de audio recording wordt automatisch verwijderd
**And** alleen de tekst-transcriptie blijft bewaard
**And** er is geen audio data meer aanwezig op het filesystem of in object storage

## Scenario 9: Resource Isolatie — Max Concurrent Bots

**Given** het maximum aantal concurrent bot containers is bereikt (configureerbaar)

**When** een gebruiker een nieuwe meeting bot probeert te starten

**Then** het systeem retourneert een foutmelding over capaciteitslimiet
**And** er worden geen extra containers gestart
**And** bestaande meetings blijven ongestoord functioneren

## Scenario 10: Infrastructuur — Service Health

**Given** alle Vexa services zijn gedeployed via docker-compose

**When** `docker compose ps` wordt uitgevoerd

**Then** vexa-meeting-api toont status "healthy"
**And** vexa-runtime-api toont status "healthy"
**And** vexa-redis toont status "healthy"
**And** vexa-transcription-service toont status "healthy"
**And** er zijn GEEN ephemeral vexa-bot containers als er geen actieve meetings zijn
**And** er is GEEN whisper-server container actief

## Scenario 11: Scribe Audio Upload via Transcription-Service

**Given** de vexa-transcription-service draait en is bereikbaar
**And** scribe-api is geconfigureerd met de nieuwe transcription-service URL

**When** een gebruiker een audio bestand uploadt via Scribe

**Then** scribe-api stuurt een POST /v1/audio/transcriptions request met tier=deferred
**And** de transcription-service verwerkt het bestand als er capaciteit is
**And** de transcriptie wordt geretourneerd in hetzelfde format als voorheen (OpenAI-compatible)
**And** de gebruiker ziet geen verschil in functionaliteit t.o.v. de oude whisper-server

## Scenario 12: Concurrent Meeting + Scribe Upload — Prioriteit

**Given** een meeting bot is actief en streamt audio (tier=realtime)
**And** de transcription-service verwerkt de meeting audio met prioriteit

**When** tegelijkertijd een Scribe audio upload binnenkomt (tier=deferred)

**Then** de realtime meeting transcriptie wordt NIET onderbroken of vertraagd
**And** de deferred Scribe upload wordt verwerkt als er vrije slots zijn
**And** als alle slots bezet zijn retourneert de service 503 met Retry-After header
**And** scribe-api buffert en probeert opnieuw na de Retry-After periode

## Scenario 13: Transcription-Service Health en Capaciteit

**Given** de vexa-transcription-service draait met MAX_CONCURRENT_TRANSCRIPTIONS=20
**And** REALTIME_RESERVED_SLOTS=1

**When** het health check endpoint wordt aangeroepen

**Then** de service retourneert status "healthy" met capaciteitsinformatie
**And** realtime slots zijn gereserveerd voor actieve meetings
**And** deferred slots worden dynamisch toegewezen aan Scribe uploads

## Scenario 14: Whisper-Server Verwijdering Validatie

**Given** de migratie is voltooid en vexa-transcription-service draait

**When** `docker compose ps` wordt uitgevoerd

**Then** er is GEEN whisper-server container actief
**And** vexa-transcription-service toont status "healthy"
**And** scribe-api verwerkt audio uploads via de nieuwe service
**And** meeting transcriptie verloopt via de nieuwe service
**And** geen enkele service refereert nog naar de oude whisper-server URL

---

## Edge Cases

### EC-1: Webhook Delivery Failure
**Given** Vexa webhook delivery faalt (netwerk issue, portal-api down)
**When** de bot_poller draait als fallback
**Then** de meeting status wordt alsnog bijgewerkt via polling

### EC-2: Transcription-Service Backpressure
**Given** meerdere meetings tegelijk actief zijn en alle transcription slots bezet
**When** een nieuw deferred transcriptie request binnenkomt
**Then** de transcription-service retourneert 503 met Retry-After header (fail-fast)
**And** de client (scribe-api of meetings.py) buffert en probeert opnieuw
**And** realtime meeting segmenten worden NIET beïnvloed (gereserveerde slots)

### EC-3: Meeting URL Niet Herkend
**Given** een gebruiker voert een ongeldige of niet-ondersteunde meeting URL in
**When** portal-api de URL valideert
**Then** een duidelijke foutmelding wordt getoond (ondersteund: Google Meet, Teams)

### EC-4: Transcription-Service Volledig Down
**Given** de vexa-transcription-service is niet bereikbaar
**When** scribe-api een audio upload probeert te verwerken
**Then** scribe-api retourneert een duidelijke foutmelding
**And** het audio bestand wordt bewaard voor latere verwerking
**And** scribe-api crasht NIET

### EC-5: Duplicate Bot Start
**Given** een bot is al actief voor dezelfde meeting
**When** een gebruiker opnieuw probeert te starten
**Then** het systeem retourneert de bestaande meeting status (geen duplicate bot)

---

## Quality Gates

### Performance
- Bot container start time: < 60 seconden van request tot meeting join
- Transcriptie latency: < 10 seconden van gesproken woord tot beschikbaar segment
- Portal API response time: < 2 seconden voor bot start/stop requests

### Reliability
- Bot_poller fallback detecteert missed webhooks binnen 30 seconden
- Service recovery: meeting-api en runtime-api herstarten automatisch na crash
- Geen data loss bij service restart (state in Redis + PostgreSQL)

### Security
- Webhook signature verificatie op alle inkomende Vexa webhooks
- Admin token niet gelogd of exposed in error messages
- Docker socket proxy beperkt tot container create/start/stop/remove operaties
- Geen secrets in docker-compose.yml (alleen via .env referenties)

### Transcription-Service
- Concurrent transcriptions: max 20 simultaan
- Realtime reserved slots: minimaal 1 (configureerbaar)
- Fail-fast: 503 response < 100ms bij overbelasting (geen queuing)
- Hallucination detection: compression_ratio > 1.8 → automatic retry
- Scribe audio upload: < 30 seconden voor een 5-minuten fragment
- Model loading: large-v3-turbo geladen bij startup, geen cold start per request

### GDPR
- Alle audio verwerking op Klai's eigen EU-servers
- Recordings verwijderd na transcriptie voltooiing
- Geen data transfer naar externe services (transcription-service is intern)
- Audit trail voor recording creation en deletion
