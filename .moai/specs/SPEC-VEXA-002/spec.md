---
id: SPEC-VEXA-002
version: "1.0"
status: draft
created: 2026-04-02
author: MoAI
priority: medium
depends_on: SPEC-VEXA-001
---

# SPEC-VEXA-002: Vexa Transcription-Service Migratie

## HISTORY

| Versie | Datum | Auteur | Wijziging |
|--------|-------|--------|-----------|
| 1.0 | 2026-04-02 | MoAI | Initieel SPEC document |

---

## 1. Context

Klai gebruikt momenteel een custom `whisper-server` (146 regels Python code) op gpu-01 voor alle transcriptie. Deze server is gestart als minimale oplossing maar heeft fundamentele beperkingen die productie-gebruik belemmeren:

- **Globale asyncio.Lock** — maximaal 1 concurrent transcriptie request
- **Geen prioriteitssysteem** — meeting real-time en Scribe uploads concurreren gelijk
- **Geen hallucination detection** — geen compression_ratio/logprob controle
- **Geen VAD** — geen Voice Activity Detection voor betere segmentatie
- **Geen temperature fallback** — geen automatische retry met hogere temperature bij slechte output
- **Geen horizontale schaalbaarheid** — single worker, single container

### Huidige Infrastructuur

| Component | Details |
|-----------|---------|
| Server | gpu-01 — Hetzner GEX44 #2963286, IP 5.9.10.215, Falkenstein FSN1-DC13 |
| GPU | NVIDIA RTX 4000 SFF Ada, 20GB VRAM |
| Whisper-server | docker-compose.gpu.yml regels 93-119, poort 127.0.0.1:8000 |
| SSH tunnel | core-01 naar gpu-01 via `gpu-tunnel.service`, maakt whisper-server beschikbaar op 172.18.0.1:8000 in core-01's Docker netwerk |
| Model | faster-whisper large-v3-turbo (~3GB VRAM) |

### Consumers van whisper-server

1. **vexa-meeting-api** (na SPEC-VEXA-001): Gebruikt `TRANSCRIBER_URL` (http://172.18.0.1:8000/v1/audio/transcriptions) voor meeting audio
2. **scribe-api**: Gebruikt `WhisperHttpProvider` (http://172.18.0.1:8000) via `klai-scribe/scribe-api/app/services/providers.py` voor audio file uploads

### Vexa Transcription-Service

Vexa's `transcription-service` (uit `feature/agentic-runtime` branch) is een production-grade vervanging met:

- **20 concurrent transcriptions** (configureerbaar via `MAX_CONCURRENT_TRANSCRIPTIONS`)
- **Two-tier admission control**: `realtime` (gereserveerde slots voor meetings) + `deferred` (best-effort voor Scribe)
- **Hallucination detection**: compression_ratio > 1.8, avg_logprob < -1.0
- **Silero VAD** met per-request overrides
- **Temperature fallback chain**: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
- **Nginx load balancer** (least-connections)
- **Zelfde API**: POST /v1/audio/transcriptions (OpenAI-compatible, drop-in vervanging)
- **Zelfde model**: faster-whisper met large-v3-turbo (~3GB VRAM)

### Relatie met SPEC-VEXA-001

SPEC-VEXA-001 beschrijft de volledige meeting bot migratie naar Vexa's agentic-runtime architectuur. De transcription-service was daar initieel onderdeel van, maar is afgesplitst naar deze aparte SPEC vanwege:

- **Aparte server**: gpu-01 vereist CUDA build, apart van core-01
- **Onafhankelijke deployment**: Kan voorafgaand aan of na de meeting bot migratie gedeployed worden
- **Eigen test scope**: GPU-specifieke validatie, VRAM management, concurrent transcription testing

**Prerequisite**: SPEC-VEXA-001 moet voltooid zijn voordat de meeting-api consumer gemigreerd kan worden. Scribe-api kan onafhankelijk gemigreerd worden.

---

## 2. Requirements (EARS Format)

### 2.1 Ubiquitous Requirements

**[REQ-U-001]** Het systeem SHALL alle transcriptie verwerking exclusief uitvoeren op EU-infrastructuur (gpu-01, Hetzner Falkenstein).

### 2.2 Event-Driven Requirements

**[REQ-E-001]** WHEN een meeting bot audio segmenten verstuurt voor transcriptie, THE SYSTEM SHALL deze verwerken met `tier=realtime` (gereserveerde slots) voor prioriteit boven batch-verzoeken.

**[REQ-E-002]** WHEN Scribe (klai-scribe) een audio bestand uploadt voor transcriptie, THE SYSTEM SHALL dit verwerken met `tier=deferred` (best-effort) via dezelfde transcription-service.

**[REQ-E-003]** WHEN de transcription-service overbelast is en een deferred request ontvangt, THE SYSTEM SHALL een 503 met `Retry-After` header retourneren (fail-fast backpressure).

**[REQ-E-004]** WHEN hallucination wordt gedetecteerd (compression_ratio > 1.8 OF avg_logprob < -1.0), THE SYSTEM SHALL automatisch een retry uitvoeren met de volgende hogere temperature uit de fallback chain [0.0, 0.2, 0.4, 0.6, 0.8, 1.0].

### 2.3 State-Driven Requirements

**[REQ-S-001]** WHILE de vexa-transcription-service draait, THE SYSTEM SHALL een health check endpoint aanbieden dat capaciteitsinformatie bevat (totale slots, bezette slots, beschikbare realtime/deferred slots).

**[REQ-S-002]** WHILE er actieve meetings zijn, THE SYSTEM SHALL realtime slots gereserveerd houden voor meeting audio (configureerbaar via `REALTIME_RESERVED_SLOTS`, standaard 1).

### 2.4 Unwanted Behavior Requirements

**[REQ-N-001]** IF de transcription-service niet beschikbaar is, THEN scribe-api SHALL een duidelijke foutmelding retourneren aan de gebruiker EN het audio bestand bewaren voor latere verwerking, NIET data verliezen.

**[REQ-N-002]** IF de transcription-service een 503 (overbelast) retourneert voor een deferred request, THEN de client SHALL het verzoek bufferen en na de `Retry-After` periode opnieuw proberen, NIET het verzoek verwerpen.

### 2.5 Optional Feature Requirements

**[REQ-O-001]** WHERE VAD (Voice Activity Detection) configuratie per request gewenst is, THE SYSTEM SHALL per-request VAD overrides ondersteunen via request parameters.

---

## 3. Architectuur

### 3.1 Nieuwe Service

| Service | Image | Locatie | Functie | Resources |
|---------|-------|---------|---------|-----------|
| `vexa-transcription-service` | Vexa agentic-runtime CUDA build | gpu-01 | Unified transcription backend: two-tier admission (realtime/deferred), hallucination detection, VAD, temperature fallback, Nginx LB | ~512MB RAM + GPU (~3GB VRAM) |

### 3.2 Verwijderd Component

| Component | Reden |
|-----------|-------|
| `whisper-server` | Vervangen door vexa-transcription-service (production-grade, two-tier, 20 concurrent, hallucination detection) |

### 3.3 Ongewijzigde Componenten

| Component | Toelichting |
|-----------|-------------|
| SSH tunnel (`gpu-tunnel.service`) | Blijft ongewijzigd als dezelfde poort (8000) behouden wordt |
| `TRANSCRIBER_URL` (vexa-meeting-api) | http://172.18.0.1:8000/v1/audio/transcriptions — ongewijzigd als poort gelijk blijft |
| `WHISPER_SERVER_URL` (scribe-api) | URL wijzigt mogelijk niet als poort gelijk blijft, maar `tier=deferred` parameter moet toegevoegd worden |

### 3.4 Netwerk Topologie

```
gpu-01 (Hetzner GEX44, Falkenstein)
  └── vexa-transcription-service (127.0.0.1:8000)
        ├── Nginx LB (least-connections)
        ├── faster-whisper workers (large-v3-turbo, CUDA)
        ├── Silero VAD
        └── Hallucination detector

        ↑ SSH tunnel (gpu-tunnel.service)
        │
core-01 (172.18.0.1:8000)
  ├── vexa-meeting-api ──→ POST /v1/audio/transcriptions (tier=realtime)
  └── scribe-api ──→ POST /v1/audio/transcriptions (tier=deferred)
```

### 3.5 Key Insight: Port Hergebruik

Als de vexa-transcription-service op dezelfde poort (127.0.0.1:8000) draait als de huidige whisper-server:
- SSH tunnel (`gpu-tunnel.service`) blijft ongewijzigd
- `TRANSCRIBER_URL` op core-01 blijft ongewijzigd (172.18.0.1:8000)
- `WHISPER_SERVER_URL` op core-01 blijft ongewijzigd
- Enige wijziging aan consumers: `tier` parameter toevoegen aan requests

---

## 4. Scope

### In Scope

- Build vexa-transcription-service Docker image met CUDA support vanuit `feature/agentic-runtime` branch
- Deploy op gpu-01 (vervang whisper-server in docker-compose.gpu.yml)
- Scribe-api `WhisperHttpProvider` URL swap en `tier=deferred` parameter toevoegen
- Vexa-meeting-api `TRANSCRIBER_URL` update (indien port wijzigt) en `tier=realtime` parameter
- Health endpoint met capaciteitsinformatie
- Whisper-server met pensioen (verwijderen uit docker-compose.gpu.yml)
- Whisper-server broncode verwijderen (`klai-scribe/whisper-server/`)
- Validatie: concurrent meeting + Scribe prioriteit enforcement

### Buiten Scope

- Meeting bot wijzigingen (SPEC-VEXA-001)
- Nieuwe GPU hardware aanschaf
- Zoom ondersteuning
- Horizontale scaling over meerdere GPU servers
- WebSocket streaming naar frontend
- Vexa agent-api, dashboard, admin-api, calendar-service, Telegram bot

---

## 5. Technische Constraints

- **GPU server**: Moet draaien op gpu-01 (RTX 4000 SFF Ada, 20GB VRAM)
- **API interface**: Zelfde `POST /v1/audio/transcriptions` (OpenAI-compatible) voor zero-change consumers
- **CUDA build**: Image moet gebuild worden vanuit Vexa `feature/agentic-runtime` branch met CUDA support
- **VRAM**: ~3GB voor large-v3-turbo model (zelfde als huidige whisper-server)
- **SSH tunnel**: Bestaande `gpu-tunnel.service` van core-01 naar gpu-01, of equivalent netwerk routing
- **Port**: Voorkeur 127.0.0.1:8000 (zelfde als whisper-server) voor minimale consumer-wijzigingen
- **Docker runtime**: NVIDIA Container Toolkit vereist op gpu-01

---

## 6. Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| SPEC-VEXA-001 voltooid | Extern | Vereist voor meeting-api consumer migratie |
| Vexa `feature/agentic-runtime` branch | Extern | Actief in ontwikkeling |
| gpu-01 access (SSH + Docker) | Intern | Beschikbaar |
| NVIDIA Container Toolkit op gpu-01 | Intern | Geinstalleerd (huidige whisper-server gebruikt GPU) |
| SSH tunnel `gpu-tunnel.service` | Intern | Draait (core-01 naar gpu-01) |
| scribe-api `WhisperHttpProvider` | Intern | Bestaand, URL swap nodig + tier parameter |
| vexa-meeting-api `TRANSCRIBER_URL` | Intern | Na SPEC-VEXA-001, tier parameter toevoegen |
