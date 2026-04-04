# SPEC-VEXA-001: Acceptance Criteria — Vexa Agentic-Runtime Migratie

## Scenario 1: Google Meet Bot Join en Transcriptie

**Given** de Vexa microservices (meeting-api, runtime-api, vexa-redis) draaien op core-01
**And** een gebruiker is ingelogd in de Klai portal met een geldige organisatie

**When** de gebruiker een Google Meet bot start via de portal met een geldige meeting URL

**Then** portal-api stuurt een POST /bots request naar vexa-meeting-api
**And** vexa-runtime-api spawnt een ephemeral vexa-bot container
**And** de bot joint de Google Meet als browser participant
**And** de meeting status in de portal database wordt "in_progress"
**And** transcript segmenten worden real-time verwerkt via Vexa's pipeline en de bestaande whisper-server
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
**And** er zijn GEEN ephemeral vexa-bot containers als er geen actieve meetings zijn
**And** de bestaande whisper-server op gpu-01 is bereikbaar via SSH tunnel

---

## Edge Cases

### EC-1: Webhook Delivery Failure
**Given** Vexa webhook delivery faalt (netwerk issue, portal-api down)
**When** de bot_poller draait als fallback
**Then** de meeting status wordt alsnog bijgewerkt via polling

### EC-2: Whisper-Server Overbelast
**Given** meerdere meetings tegelijk actief zijn en whisper-server overbelast raakt
**When** een nieuw transcriptie request binnenkomt
**Then** de meeting-api retourneert transcript segments zodra beschikbaar
**And** er gaat geen data verloren

### EC-3: Meeting URL Niet Herkend
**Given** een gebruiker voert een ongeldige of niet-ondersteunde meeting URL in
**When** portal-api de URL valideert
**Then** een duidelijke foutmelding wordt getoond (ondersteund: Google Meet, Teams)

### EC-4: Duplicate Bot Start
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

### GDPR
- Alle audio verwerking op Klai's eigen EU-servers
- Recordings verwijderd na transcriptie voltooiing
- Geen data transfer naar externe services (whisper-server draait op eigen gpu-01 server)
- Audit trail voor recording creation en deletion
