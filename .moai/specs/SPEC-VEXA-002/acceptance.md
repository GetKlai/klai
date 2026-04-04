# SPEC-VEXA-002: Acceptance Criteria — Vexa Transcription-Service Migratie

## Scenario 1: Scribe Audio Upload via Transcription-Service (tier=deferred)

**Given** de vexa-transcription-service draait op gpu-01 en is bereikbaar via SSH tunnel op 172.18.0.1:8000
**And** scribe-api is geconfigureerd met de transcription-service URL
**And** een gebruiker is ingelogd in Klai en heeft een audio bestand

**When** de gebruiker een audio bestand uploadt via Scribe

**Then** scribe-api stuurt een POST /v1/audio/transcriptions request met `tier=deferred`
**And** de transcription-service verwerkt het bestand als er capaciteit is
**And** de transcriptie wordt geretourneerd in OpenAI-compatible format (zelfde als oude whisper-server)
**And** de gebruiker ziet geen verschil in functionaliteit t.o.v. de oude whisper-server

## Scenario 2: Meeting Audio Transcriptie (tier=realtime)

**Given** de vexa-transcription-service draait op gpu-01
**And** vexa-meeting-api is geconfigureerd met TRANSCRIBER_URL (na SPEC-VEXA-001)
**And** een meeting bot is actief en streamt audio segmenten

**When** de meeting bot een audio segment verstuurt voor transcriptie

**Then** het request wordt verwerkt met `tier=realtime` (gereserveerde slot)
**And** de transcriptie latency is < 10 seconden
**And** de meeting transcriptie wordt niet onderbroken door deferred requests
**And** het transcript segment wordt geretourneerd in het verwachte format

## Scenario 3: Concurrent Meeting + Scribe Upload — Prioriteit Enforcement

**Given** een meeting bot is actief en streamt audio (tier=realtime)
**And** de transcription-service verwerkt meeting audio met prioriteit
**And** alle vrije slots zijn bezet door deferred requests

**When** tegelijkertijd een Scribe audio upload binnenkomt (tier=deferred)

**Then** de realtime meeting transcriptie wordt NIET onderbroken of vertraagd
**And** als er een vrij deferred slot vrijkomt wordt de Scribe upload verwerkt
**And** als alle slots bezet zijn retourneert de service 503 met `Retry-After` header
**And** scribe-api buffert het request en probeert opnieuw na de Retry-After periode

## Scenario 4: Transcription-Service Health en Capaciteit

**Given** de vexa-transcription-service draait met `MAX_CONCURRENT_TRANSCRIPTIONS=20`
**And** `REALTIME_RESERVED_SLOTS=1`

**When** het health check endpoint wordt aangeroepen (`GET /health`)

**Then** de service retourneert HTTP 200 met status "healthy"
**And** het response bevat capaciteitsinformatie:
  - totale slots (20)
  - bezette slots (huidige load)
  - beschikbare realtime slots
  - beschikbare deferred slots
**And** realtime slots zijn gereserveerd voor meeting audio

## Scenario 5: Hallucination Detection en Retry

**Given** de transcription-service verwerkt een audio segment
**And** hallucination detection is actief

**When** de initiële transcriptie een compression_ratio > 1.8 OF avg_logprob < -1.0 heeft

**Then** de service voert automatisch een retry uit met de volgende hogere temperature
**And** de temperature fallback chain is [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
**And** het uiteindelijke resultaat wordt geretourneerd (beste poging)
**And** de client merkt niets van de interne retries (transparant)

## Scenario 6: Service Down — Graceful Degradation

**Given** de vexa-transcription-service is niet bereikbaar (gpu-01 down, service crashed)

**When** scribe-api een audio upload probeert te verwerken

**Then** scribe-api retourneert een duidelijke foutmelding aan de gebruiker
**And** het audio bestand wordt bewaard voor latere verwerking
**And** scribe-api crasht NIET
**And** er is geen data verlies

## Scenario 7: Whisper-Server Verwijdering Validatie

**Given** de migratie is voltooid en vexa-transcription-service draait op gpu-01

**When** `docker compose -f docker-compose.gpu.yml ps` wordt uitgevoerd op gpu-01

**Then** er is GEEN whisper-server container actief
**And** vexa-transcription-service toont status "healthy"
**And** scribe-api verwerkt audio uploads via de nieuwe service
**And** meeting transcriptie verloopt via de nieuwe service (na SPEC-VEXA-001)
**And** geen enkele service refereert nog naar de oude whisper-server specifieke configuratie

## Scenario 8: GPU Memory en Performance

**Given** de vexa-transcription-service draait met large-v3-turbo model op RTX 4000 SFF Ada (20GB VRAM)

**When** `nvidia-smi` wordt uitgevoerd op gpu-01

**Then** VRAM gebruik is ~3GB (vergelijkbaar met oude whisper-server)
**And** er is voldoende VRAM beschikbaar voor concurrent transcriptions
**And** geen GPU OOM (Out of Memory) errors in service logs

---

## Edge Cases

### EC-1: SSH Tunnel Onderbreking Tijdens Actieve Transcriptie

**Given** een transcriptie request is onderweg via de SSH tunnel
**When** de SSH tunnel tijdelijk onderbroken wordt (netwerk issue)
**Then** het lopende request faalt met een timeout error
**And** de client (scribe-api/meeting-api) kan opnieuw proberen na tunnel herstel
**And** de transcription-service zelf blijft stabiel (geen half-verwerkte state)
**And** `gpu-tunnel.service` herstart de tunnel automatisch via systemd

### EC-2: GPU Out of Memory

**Given** de transcription-service draait met maximale concurrent workers
**When** GPU VRAM onvoldoende is voor een nieuwe transcriptie worker

**Then** de service retourneert een foutmelding (niet een crash)
**And** bestaande transcripties worden niet beïnvloed
**And** de fout wordt gelogd met GPU memory details
**And** nieuwe requests worden geweigerd tot VRAM vrijkomt

### EC-3: 503 Backpressure met Retry-After

**Given** alle transcriptie slots zijn bezet (20/20)
**When** een nieuw deferred request binnenkomt

**Then** de service retourneert 503 met `Retry-After: <seconds>` header
**And** de response time voor de 503 is < 100ms (fail-fast, geen queuing)
**And** de client respecteert de Retry-After waarde
**And** realtime slots worden NIET vrijgegeven voor deferred requests

---

## Quality Gates

### Performance
- Scribe audio upload (5 minuten audio): transcriptie < 30 seconden
- Realtime meeting segment: latency < 10 seconden
- Health check endpoint: response < 200ms
- 503 fail-fast response: < 100ms
- Model loading bij startup: < 120 seconden

### Reliability
- Auto-restart via Docker `unless-stopped` restart policy
- Geen data verlies bij service restart (stateless transcriptie)
- SSH tunnel auto-recovery via systemd
- Health check detecteert niet-functionerende service binnen 30 seconden

### Transcriptie Kwaliteit
- Hallucination detection: compression_ratio > 1.8 triggert retry
- Hallucination detection: avg_logprob < -1.0 triggert retry
- VAD: Silero VAD actief voor betere segmentatie
- Temperature fallback: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0] voor optimale output
- Model: large-v3-turbo (zelfde kwaliteit als huidige whisper-server)

### GDPR
- Alle transcriptie verwerking op gpu-01 (Hetzner Falkenstein, EU/Duitsland)
- Geen audio data transfer naar externe services
- Audio bestanden worden niet persistent opgeslagen door de transcription-service
- Transcriptie resultaten worden alleen geretourneerd aan de aanvragende service
