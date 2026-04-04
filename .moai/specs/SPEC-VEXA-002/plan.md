# SPEC-VEXA-002: Implementatieplan — Vexa Transcription-Service Migratie

## Overzicht

Migratie van Klai's custom `whisper-server` (146 regels Python, single worker, globale lock) naar Vexa's production-grade `transcription-service` op gpu-01. Dit is een drop-in vervanging met dezelfde API interface (`POST /v1/audio/transcriptions`) maar met 20x concurrency, two-tier priority, hallucination detection, en VAD.

## Gebruikersbeslissingen (te bevestigen)

| Vraag | Standaard | Toelichting |
|-------|-----------|-------------|
| SSH tunnel of directe netwerk routing? | SSH tunnel behouden | Zelfde poort = geen wijzigingen op core-01 |
| Max concurrent transcriptions? | 20 | Configureerbaar via `MAX_CONCURRENT_TRANSCRIPTIONS` |
| Realtime reserved slots? | 1 | Configureerbaar via `REALTIME_RESERVED_SLOTS` |
| Rollback plan als transcription-service faalt? | Whisper-server image behouden als backup | Terugzetten door image swap in docker-compose.gpu.yml |

## Architectuur: Huidig vs Nieuw

### Huidig (wordt vervangen)
```
gpu-01:
  whisper-server (127.0.0.1:8000)
    ├── FastAPI (146 regels code)
    ├── asyncio.Lock (1 concurrent request)
    ├── faster-whisper large-v3-turbo
    └── Geen priority / geen VAD / geen hallucination detection

    ↑ SSH tunnel (gpu-tunnel.service)
    │
core-01 (172.18.0.1:8000):
  ├── vexa-meeting-api → TRANSCRIBER_URL
  └── scribe-api → WHISPER_SERVER_URL (WhisperHttpProvider)
```

### Nieuw (te deployen)
```
gpu-01:
  vexa-transcription-service (127.0.0.1:8000)  ← zelfde poort!
    ├── Nginx LB (least-connections)
    ├── 20 concurrent faster-whisper workers
    ├── Two-tier: realtime (reserved) + deferred (best-effort)
    ├── Hallucination detection (compression_ratio, logprob)
    ├── Silero VAD (per-request overrides)
    ├── Temperature fallback chain
    └── faster-whisper large-v3-turbo (zelfde model)

    ↑ SSH tunnel (gpu-tunnel.service) — ONGEWIJZIGD
    │
core-01 (172.18.0.1:8000) — ONGEWIJZIGD:
  ├── vexa-meeting-api → TRANSCRIBER_URL (+ tier=realtime parameter)
  └── scribe-api → WHISPER_SERVER_URL (+ tier=deferred parameter)
```

### Key Insight

Door dezelfde poort (127.0.0.1:8000) te hergebruiken op gpu-01:
- SSH tunnel (`gpu-tunnel.service`) blijft ongewijzigd
- Geen wijzigingen aan core-01 netwerk configuratie
- Consumer URLs blijven identiek
- Enige wijziging: `tier` parameter toevoegen aan requests

## Taakdecompositie

### Fase 1: GPU Server Voorbereiding

**Task 1.1: Build vexa-transcription-service Docker image met CUDA support**
- Clone/checkout Vexa `feature/agentic-runtime` branch
- Build `packages/transcription-service` Docker image met CUDA support
- Pin op specifieke commit hash (geen `:latest` tag)
- Verify: image bevat CUDA runtime, faster-whisper, Nginx, Silero VAD
- Output: `ghcr.io/getklai/vexa-transcription-service:<commit-hash>`

**Task 1.2: Push image naar ghcr.io/getklai/ registry**
- Tag image met commit hash en `latest`
- Push naar GitHub Container Registry
- Verify: `docker pull` werkt op gpu-01

**Task 1.3: Update docker-compose.gpu.yml**
- Verwijder `whisper-server` service definitie (regels 93-119)
- Voeg `vexa-transcription-service` service toe met:
  - Image: `ghcr.io/getklai/vexa-transcription-service:<commit-hash>`
  - Port: `127.0.0.1:8000:8000` (zelfde als whisper-server)
  - GPU access: `deploy.resources.reservations.devices` (NVIDIA runtime)
  - Environment:
    - `MAX_CONCURRENT_TRANSCRIPTIONS=20`
    - `REALTIME_RESERVED_SLOTS=1`
    - `FAIL_FAST_WHEN_BUSY=true`
    - `MODEL_NAME=large-v3-turbo`
    - `ENABLE_VAD=true`
  - Volumes: model cache directory (voorkom cold download bij restart)
  - Healthcheck: `curl -f http://localhost:8000/health`
  - Restart policy: `unless-stopped`

### Fase 2: Deployment op gpu-01

**Task 2.1: Pull nieuw image op gpu-01**
- SSH naar gpu-01
- `docker pull ghcr.io/getklai/vexa-transcription-service:<commit-hash>`
- Verify: image size en layers correct

**Task 2.2: Stop whisper-server, start transcription-service**
- Stap 1: Verify geen actieve transcriptie requests (check whisper-server logs)
- Stap 2: `docker compose -f docker-compose.gpu.yml stop whisper-server`
- Stap 3: `docker compose -f docker-compose.gpu.yml up -d vexa-transcription-service`
- Stap 4: Wacht op model loading (large-v3-turbo, ~30-60 seconden)
- **Downtime**: Verwacht 1-2 minuten (model loading)
- **Blue-green niet mogelijk**: Zelfde poort, zelfde GPU

**Task 2.3: Verify health endpoint, model loading, GPU access**
- `curl http://127.0.0.1:8000/health` — verwacht 200 met capaciteitsinformatie
- `docker logs vexa-transcription-service` — verify model loaded, GPU detected
- `nvidia-smi` — verify VRAM usage (~3GB voor large-v3-turbo)

**Task 2.4: Verify SSH tunnel werkt (zelfde poort = geen wijziging nodig)**
- Vanaf core-01: `curl http://172.18.0.1:8000/health`
- Verify: response bevat capaciteitsinformatie van nieuwe service
- Als tunnel niet werkt: `systemctl restart gpu-tunnel.service`

### Fase 3: Consumer Migratie

**Task 3.1: Update scribe-api voor tier=deferred**
- `klai-scribe/scribe-api/app/services/providers.py` — `WhisperHttpProvider`
- Voeg `tier=deferred` parameter toe aan transcriptie requests
- URL (`WHISPER_SERVER_URL`) blijft ongewijzigd als poort gelijk is
- Test: audio upload via scribe-api -> transcriptie via nieuwe service
- De `SpeechProvider` abstractie maakt dit triviaal (alleen tier parameter toevoegen)

**Task 3.2: Update vexa-meeting-api voor tier=realtime**
- Na SPEC-VEXA-001 voltooiing
- `TRANSCRIBER_URL` blijft ongewijzigd (172.18.0.1:8000/v1/audio/transcriptions)
- Voeg `tier=realtime` parameter toe aan meeting audio requests
- Verify: meeting transcriptie krijgt voorrang boven Scribe uploads

**Task 3.3: Voeg 503 retry-logica toe aan scribe-api**
- `WhisperHttpProvider` moet 503 met `Retry-After` header afhandelen
- Buffer request en retry na `Retry-After` periode
- Maximaal 3 retries met exponential backoff
- Na max retries: log error, bewaar audio voor handmatige verwerking

### Fase 4: Validatie & Cleanup

**Task 4.1: Test Scribe audio upload end-to-end**
- Upload audio bestand via Scribe
- Verify: transcriptie via vexa-transcription-service
- Verify: output format identiek aan oude whisper-server (OpenAI-compatible)
- Verify: geen regressie in transcriptie kwaliteit

**Task 4.2: Test meeting transcriptie end-to-end**
- Na SPEC-VEXA-001: start meeting bot
- Verify: audio segmenten verwerkt met tier=realtime
- Verify: latency < 10 seconden voor realtime segmenten

**Task 4.3: Test concurrent meeting + Scribe (prioriteit enforcement)**
- Start meeting bot (tier=realtime)
- Tegelijkertijd Scribe audio upload (tier=deferred)
- Verify: realtime niet onderbroken of vertraagd
- Verify: deferred verwerkt als er vrije slots zijn
- Verify: 503 + Retry-After als alle slots bezet

**Task 4.4: Remove whisper-server uit docker-compose.gpu.yml**
- Verwijder whisper-server service definitie (al gedaan in Task 1.3)
- Verify: `docker compose ps` toont GEEN whisper-server container
- Remove whisper-server Docker image van gpu-01 (disk cleanup)

**Task 4.5: Remove whisper-server broncode**
- Verwijder `klai-scribe/whisper-server/` directory
- Update eventuele referenties in documentatie
- Verify: geen code meer die direct naar whisper-server verwijst

## Risico's en Mitigatie

| Risico | Impact | Mitigatie |
|--------|--------|-----------|
| CUDA build complexiteit | Hoog | Gebruik Vexa's bestaande Dockerfile, test lokaal voor push |
| GPU VRAM (transcription-service meer dan whisper-server's 3GB) | Midden | Monitor `nvidia-smi`, large-v3-turbo is zelfde model = zelfde VRAM |
| SSH tunnel onderbreking tijdens swap | Midden | Swap tijdens low-traffic window, tunnel herstart automatisch |
| Meeting transcriptie downtime tijdens swap | Midden | Communiceer maintenance window, swap duurt 1-2 min |
| `feature/agentic-runtime` is geen stable release | Hoog | Pin op specifieke commit hash, niet `:latest` |
| Nginx LB configuratie compatibiliteit | Laag | Test health endpoint na deploy, fallback naar single worker |
| Scribe-api compatibility | Laag | Zelfde API interface, alleen tier parameter toevoeging |

## Rollback Plan

Als de transcription-service niet correct functioneert:

1. `docker compose -f docker-compose.gpu.yml stop vexa-transcription-service`
2. Restore whisper-server service in docker-compose.gpu.yml
3. `docker compose -f docker-compose.gpu.yml up -d whisper-server`
4. SSH tunnel herstart niet nodig (zelfde poort)
5. Scribe-api en meeting-api werken direct weer (tier parameter wordt genegeerd door whisper-server)

## Referenties

- Huidige whisper-server: `klai-scribe/whisper-server/main.py` (146 regels)
- Scribe providers: `klai-scribe/scribe-api/app/services/providers.py` (WhisperHttpProvider)
- GPU compose: `deploy/docker-compose.gpu.yml` (regels 93-119)
- SSH tunnel: `/etc/systemd/system/gpu-tunnel.service` op core-01
- Vexa transcription-service: `https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime/packages/transcription-service`
- SPEC-VEXA-001: `.moai/specs/SPEC-VEXA-001/spec.md` (meeting bot migratie)
- Infrastructure pitfalls: `.claude/rules/klai/pitfalls/infrastructure.md`

## Geschatte Complexiteit

| Fase | Complexiteit | Bestanden |
|------|-------------|-----------|
| Fase 1: GPU Server Voorbereiding | Midden | 2-3 bestanden (Dockerfile, docker-compose.gpu.yml, registry) |
| Fase 2: Deployment op gpu-01 | Laag-Midden | 1 bestand (docker-compose.gpu.yml) + server operaties |
| Fase 3: Consumer Migratie | Laag | 2-3 bestanden (providers.py, meeting-api config) |
| Fase 4: Validatie & Cleanup | Midden | Manueel + 2-3 bestanden verwijderen |
