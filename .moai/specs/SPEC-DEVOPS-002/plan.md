---
id: SPEC-DEVOPS-002
version: 1.0.0
status: draft
created: 2026-03-30
updated: 2026-03-30
author: MoAI
priority: high
---

# Implementatieplan: SPEC-DEVOPS-002 -- GPU-01 TEI + Infinity Split Herstellen

## HISTORY

| Versie | Datum      | Auteur | Wijziging                |
|--------|------------|--------|--------------------------|
| 1.0.0  | 2026-03-30 | MoAI   | Initieel plan            |

---

## Overzicht

Dit plan beschrijft de stapsgewijze uitvoering van de TEI/Infinity split-herstel op gpu-01 en de bijbehorende configuratie-updates op core-01. De volgorde is kritisch: gpu-01 wordt eerst gecorrigeerd, daarna de SSH-tunnel, dan core-01 consumers, en als laatste verificatie.

---

## Fase 1: GPU-01 Service Herconfiguratie

**Prioriteit:** Primair doel
**Risico:** Medium -- gpu-01 is een dedicated server met directe SSH-toegang
**Referenties:** REQ-GPU-001, REQ-GPU-002, REQ-GPU-003, REQ-GPU-004

### Stap 1: TEI service toevoegen aan gpu-01 docker-compose.yml

**Actie:** Bewerk `/opt/klai-gpu/docker-compose.yml` op gpu-01:
- Wijzig de huidige `infinity` service van poort 7997 naar poort 7998
- Verwijder het bge-m3 model uit de Infinity configuratie (laat alleen bge-reranker-v2-m3)
- Voeg een nieuwe `tei` service toe op poort 7997 met:
  - Image: `ghcr.io/huggingface/text-embeddings-inference:1.5`
  - Model: `BAAI/bge-m3`
  - GPU: NVIDIA runtime toewijzing
  - Volume mount voor model cache

**Pre-flight checks (CON-004):**
- Inspecteer huidige `docker compose config` op gpu-01
- Noteer huidige volumes en netwerkconfiguratie
- Maak backup: `cp docker-compose.yml docker-compose.yml.bak`

### Stap 2: Services herstarten op gpu-01

**Actie:**
```bash
ssh gpu-01
cd /opt/klai-gpu
docker compose down
docker compose up -d
```

**Post-flight checks:**
- `docker compose ps` -- alle services moeten "running" zijn
- `docker logs --tail 30 tei` -- geen errors, model geladen
- `docker logs --tail 30 infinity` -- geen errors, reranker model geladen
- `curl http://localhost:7997/health` -- TEI gezond
- `curl http://localhost:7998/health` -- Infinity gezond
- `curl http://localhost:8001/health` -- bge-m3-sparse gezond (ongewijzigd)
- `curl http://localhost:8000/health` -- whisper-server gezond (ongewijzigd)

### Stap 3: SSH-tunnel uitbreiden met poort 7998

**Actie:** Voeg poort 7998 toe aan de autossh systemd service op core-01:

```bash
# Bewerk de tunnel service op core-01
ssh core-01 "sudo nano /etc/systemd/system/gpu-tunnel.service"
```

Voeg `-L 172.18.0.1:7998:127.0.0.1:7998` toe aan de `ExecStart` regel, naast de bestaande forwardings:
```
# Bestaand (niet wijzigen):
-L 172.18.0.1:7997:127.0.0.1:7997
-L 172.18.0.1:8001:127.0.0.1:8001
-L 172.18.0.1:8000:127.0.0.1:8000
# Toevoegen:
-L 172.18.0.1:7998:127.0.0.1:7998
```

Daarna herladen en herstarten:
```bash
ssh core-01 "sudo systemctl daemon-reload && sudo systemctl restart gpu-tunnel.service"
ssh core-01 "sudo systemctl status gpu-tunnel.service"
```

**Verificatie:**
```bash
# Alle 4 tunnelpoorten bereikbaar vanuit core-01:
ssh core-01 "sudo ss -tlnp | grep 172.18.0.1"
curl -sf http://172.18.0.1:7997/health  # TEI (embeddings)
curl -sf http://172.18.0.1:7998/health  # Infinity (reranker)
curl -sf http://172.18.0.1:8001/health  # sparse
curl -sf http://172.18.0.1:8000/health  # whisper
```

### Rollback Fase 1

Als de herconfiguratie op gpu-01 faalt:
1. `ssh gpu-01 && cd /opt/klai-gpu`
2. `cp docker-compose.yml.bak docker-compose.yml`
3. `docker compose down && docker compose up -d`
4. Verifieer dat de oude configuratie weer draait

---

## Fase 2: Core-01 docker-compose.yml Update

**Prioriteit:** Primair doel
**Risico:** Hoog -- productie-impact op 6 consumer-services
**Referenties:** REQ-CORE-001, REQ-CORE-002, REQ-CORE-003, REQ-CORE-004
**Afhankelijkheid:** Fase 1 moet volledig voltooid en geverifieerd zijn

### Stap 1: Environment variabelen updaten

**Actie:** Bewerk `deploy/docker-compose.yml` in de repo:

Wijzig de 9 environment variabelen conform de mapping in REQ-CORE-001:

| Service | Variable | Oud | Nieuw |
|---------|----------|-----|-------|
| `knowledge-ingest` | `TEI_URL` | `http://tei:8080` | `http://172.18.0.1:7997` |
| `knowledge-ingest` | `SPARSE_SIDECAR_URL` | `http://bge-m3-sparse:8001` | `http://172.18.0.1:8001` |
| `retrieval-api` | `TEI_URL` | `http://tei:8080` | `http://172.18.0.1:7997` |
| `retrieval-api` | `TEI_RERANKER_URL` | `http://infinity-reranker:7997` | `http://172.18.0.1:7998` |
| `retrieval-api` | `SPARSE_SIDECAR_URL` | `http://bge-m3-sparse:8001` | `http://172.18.0.1:8001` |
| `scribe-api` | `WHISPER_SERVER_URL` | `http://whisper-server:8000` | `http://172.18.0.1:8000` |
| `vexa-bot-manager` | `TRANSCRIBER_URL` | `http://whisper-server:8000/...` | `http://172.18.0.1:8000/...` |
| `librechat-klai` | `JINA_API_URL` | `http://infinity-reranker:7997/v1/rerank` | `http://172.18.0.1:7998/v1/rerank` |
| `research-api` | `TEI_URL` | `http://tei:8080` | `http://172.18.0.1:7997` |

### Stap 2: Oude GPU service-definities verwijderen

**Actie:** Verwijder de volgende service-blokken uit `deploy/docker-compose.yml`:
- `tei` service-definitie (regels ~564-572)
- `bge-m3-sparse` service-definitie (regels ~574-595)
- `whisper-server` service-definitie (regels ~744-756)
- `infinity-reranker` service-definitie (regels ~925-945)

### Stap 3: depends_on entries verwijderen

**Actie:** Verwijder stale `depends_on` referenties:
- `research-api.depends_on`: verwijder `- tei`
- `knowledge-ingest.depends_on`: verwijder `- tei` en `- bge-m3-sparse`
- `scribe-api.depends_on`: verwijder `- whisper-server`

### Stap 4: Ongebruikte volumes verwijderen

**Actie:** Verwijder de volgende volume-definities uit de `volumes:` sectie:
- `tei-models`
- `whisper-models`
- `infinity-models` (indien niet meer in gebruik na de Infinity poortwijziging)

### Stap 5: Deployen naar core-01

**Actie:**
1. Commit en push de docker-compose.yml wijzigingen
2. Kopieer naar core-01: `scp deploy/docker-compose.yml core-01:/opt/klai/docker-compose.yml`
   (of via het gebruikelijke deploy-mechanisme)
3. Herstart de betreffende services:

```bash
ssh core-01
cd /opt/klai
# Pre-flight check (CON-004):
docker compose config knowledge-ingest | grep -A 10 'environment:'
docker compose config retrieval-api | grep -A 10 'environment:'

# Herstart consumer-services (niet de hele stack):
docker compose up -d knowledge-ingest retrieval-api scribe-api vexa-bot-manager research-api
```

**Post-flight checks:**
- `docker compose ps` -- alle services "running"
- `docker logs --tail 30 klai-core-knowledge-ingest-1` -- geen connection errors
- `docker logs --tail 30 klai-core-retrieval-api-1` -- geen connection errors
- `docker logs --tail 30 klai-core-scribe-api-1` -- geen connection errors

### Rollback Fase 2

Als de consumer-services op core-01 falen:
1. Herstel de vorige docker-compose.yml: `git checkout HEAD~1 -- deploy/docker-compose.yml`
2. Kopieer naar core-01 en herstart
3. Oude GPU services op core-01 zijn al verwijderd -- als rollback nodig is naar de volledig oude situatie, moeten de service-definities handmatig worden teruggezet

**Belangrijk:** Zolang de SSH-tunnel naar gpu-01 werkt en de services op gpu-01 draaien, zijn de 172.18.0.1 endpoints beschikbaar. Rollback is alleen nodig als de tunnel of gpu-01 services falen.

---

## Fase 3: Python Config Defaults

**Prioriteit:** Secundair doel
**Risico:** Laag -- defaults worden overschreven door env vars; dit is een defensieve verbetering
**Referenties:** REQ-PY-001, REQ-PY-002

### Stap 1: Config defaults updaten

**Actie:** Bewerk de volgende 4 bestanden in de repo:

1. `klai-knowledge-ingest/knowledge_ingest/config.py`
   - Regel 8: `tei_url` default `"http://tei:8080"` --> `"http://172.18.0.1:7997"`
   - Regel 33: `sparse_sidecar_url` default `"http://bge-m3-sparse:8001"` --> `"http://172.18.0.1:8001"`

2. `klai-retrieval-api/retrieval_api/config.py`
   - Regel 12: `tei_url` default `"http://tei:8080"` --> `"http://172.18.0.1:7997"`
   - Regel 13: `tei_reranker_url` default `"http://tei-reranker:8080"` --> `"http://172.18.0.1:7998"` (latente bug fix)
   - Regel 23: `sparse_sidecar_url` default `""` --> `"http://172.18.0.1:8001"`

3. `klai-scribe/scribe-api/app/core/config.py`
   - Regel 18: `whisper_server_url` default `"http://whisper-server:8000"` --> `"http://172.18.0.1:8000"`

**Opmerking:** Deze wijzigingen worden meegenomen in dezelfde commit als de docker-compose.yml updates.

### Rollback Fase 3

Niet nodig -- de env vars in docker-compose.yml overschrijven deze defaults altijd. Een revert van de config-bestanden is eenvoudig via git.

---

## Fase 4: Verificatie

**Prioriteit:** Primair doel (verplicht voor voltooiing)
**Risico:** Geen -- alleen lezen/testen
**Referenties:** REQ-VER-001, REQ-VER-002, REQ-VER-003, REQ-VER-004
**Afhankelijkheid:** Fase 1, 2 en 3 moeten volledig voltooid zijn

### Stap 1: Health checks via SSH-tunnel

```bash
# Vanuit core-01:
curl -s http://172.18.0.1:7997/health   # TEI (embeddings)
curl -s http://172.18.0.1:7998/health   # Infinity (reranker)
curl -s http://172.18.0.1:8001/health   # BGE-M3 sparse
curl -s http://172.18.0.1:8000/health   # Whisper
```

Alle 4 endpoints moeten een 200 OK response geven.

### Stap 2: Test embedding-aanroep

```bash
# Test embedding via TEI:
curl -s -X POST http://172.18.0.1:7997/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input": "test embedding", "model": "BAAI/bge-m3"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'dims={len(d[\"data\"][0][\"embedding\"])}')"
```

Verwacht resultaat: `dims=1024` (bge-m3 dimensionaliteit).

### Stap 3: Test rerank-aanroep

```bash
# Test reranking via Infinity:
curl -s -X POST http://172.18.0.1:7998/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"model": "BAAI/bge-reranker-v2-m3", "query": "test", "documents": ["doc1", "doc2"]}'
```

Verwacht resultaat: JSON met `results` array en relevancy scores.

### Stap 4: Verifieer geen oude GPU-containers op core-01

```bash
# Op core-01:
docker ps --format '{{.Names}}' | grep -E '(tei|bge-m3-sparse|whisper-server|infinity-reranker)'
```

Verwacht resultaat: lege output (geen matches).

### Stap 5: End-to-end controle consumer-services

```bash
# Controleer logs op connection errors:
docker logs --tail 50 klai-core-knowledge-ingest-1 2>&1 | grep -i error
docker logs --tail 50 klai-core-retrieval-api-1 2>&1 | grep -i error
docker logs --tail 50 klai-core-scribe-api-1 2>&1 | grep -i error
docker logs --tail 50 klai-core-research-api-1 2>&1 | grep -i error
```

Verwacht resultaat: geen connection errors naar de inference endpoints.

---

## Technische Aanpak

### Waarom TEI voor embeddings in plaats van Infinity

| Criterium | TEI | Infinity |
|-----------|-----|----------|
| Architectuur | Rust + cuBLASLt (HuggingFace native) | Python + PyTorch |
| GPU memory | Stabiel, geen bekende leaks | Bekende leak (issue #517, open) |
| bge-m3 ondersteuning | Aanbevolen op model card | Ondersteund maar niet aanbevolen |
| Prometheus metrics | Native beschikbaar | Beperkt |
| Batching | Token-based dynamic batching | Request-based batching |

### Waarom Infinity voor reranking in plaats van TEI

| Criterium | TEI | Infinity |
|-----------|-----|----------|
| LiteLLM integratie | Geen native rerank support | Cohere-compatible `/rerank` endpoint |
| bge-reranker-v2-m3 | Werkt maar geen native rerank API | Native rerank ondersteuning |
| Operationeel | Tweede TEI instantie nodig met ander model | Eenvoudige configuratie |

### Poortschema

```
gpu-01 (intern)        SSH-tunnel         core-01 (172.18.0.1)
-----------------     ------------>       ----------------------
:7997 (TEI)           -L 7997            :7997 (dense embeddings)
:7998 (Infinity)      -L 7998            :7998 (reranking)
:8000 (Whisper)       -L 8000            :8000 (STT)
:8001 (Sparse)        -L 8001            :8001 (sparse embeddings)
```

---

## Risico's en Mitigatie

| Risico | Impact | Kans | Mitigatie |
|--------|--------|------|-----------|
| TEI start niet op gpu-01 | Embeddings onbeschikbaar | Laag | Rollback naar Infinity op poort 7997; model werkt bewezen op TEI |
| SSH-tunnel poort 7998 faalt | Reranking onbeschikbaar | Laag | Debug tunnel; poort forwarding is standaard SSH functionaliteit |
| Consumer-services vinden endpoints niet | Embedding/rerank/STT uitval | Medium | Pre-flight check env vars; rollback docker-compose.yml via git |
| VRAM te krap voor TEI + Infinity naast elkaar | OOM op gpu-01 | Laag | RTX 4000 Ada heeft 20GB; TEI (bge-m3) ~2GB + Infinity (reranker) ~1GB = ruim voldoende |
| embedder.py incompatibel met TEI | Embedding calls falen | Zeer laag | Commit 235a259 schakelde al naar `/v1/embeddings` (OpenAI-compatible), wat TEI ondersteunt |

---

## Scope-beperkingen

- LibreChat (`librechat-klai`) env var update is opgenomen maar de service wordt niet actief getest (LibreChat is een third-party service)
- embedder.py wordt NIET gewijzigd (reeds compatible, zie CON-007)
- Geen wijzigingen aan de modellen zelf -- alleen de serving-infrastructuur verandert
