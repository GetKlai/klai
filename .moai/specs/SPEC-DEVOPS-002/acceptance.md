---
id: SPEC-DEVOPS-002
version: 1.0.0
status: draft
created: 2026-03-30
updated: 2026-03-30
author: MoAI
priority: high
---

# Acceptatiecriteria: SPEC-DEVOPS-002 -- GPU-01 TEI + Infinity Split Herstellen

## HISTORY

| Versie | Datum      | Auteur | Wijziging                |
|--------|------------|--------|--------------------------|
| 1.0.0  | 2026-03-30 | MoAI   | Initieel document        |

---

## Module 1: GPU-01 Service Herconfiguratie

### AC-GPU-001: TEI service draait op poort 7997

**Given** gpu-01 Docker Compose stack is geconfigureerd met een `tei` service
**And** de `tei` service gebruikt image `ghcr.io/huggingface/text-embeddings-inference:1.5`
**And** het model is ingesteld op `BAAI/bge-m3`
**When** de Docker Compose stack wordt gestart op gpu-01
**Then** de `tei` container start succesvol op
**And** `curl http://localhost:7997/health` retourneert HTTP 200
**And** `docker logs tei` toont dat het BAAI/bge-m3 model is geladen

### AC-GPU-002: Infinity service draait op poort 7998 (alleen reranking)

**Given** gpu-01 Docker Compose stack is geconfigureerd met een `infinity` service op poort 7998
**And** de `infinity` service is geconfigureerd met uitsluitend het model `BAAI/bge-reranker-v2-m3`
**When** de Docker Compose stack wordt gestart op gpu-01
**Then** de `infinity` container start succesvol op
**And** `curl http://localhost:7998/health` retourneert HTTP 200
**And** `docker logs infinity` toont dat alleen het reranker model is geladen
**And** Infinity bedient GEEN embedding requests (geen bge-m3 model geladen)

### AC-GPU-003: Bestaande services ongewijzigd

**Given** gpu-01 Docker Compose stack bevat `bge-m3-sparse` (poort 8001) en `whisper-server` (poort 8000)
**When** de Docker Compose stack wordt herstart na de TEI/Infinity wijzigingen
**Then** `curl http://localhost:8001/health` retourneert HTTP 200
**And** `curl http://localhost:8000/health` retourneert HTTP 200
**And** de configuratie van `bge-m3-sparse` en `whisper-server` is ongewijzigd ten opzichte van de vorige versie

### AC-GPU-004: SSH-tunnel forwardt poort 7998

**Given** de SSH-tunnel van core-01 naar gpu-01 is geconfigureerd
**And** de tunnel-configuratie bevat `-L 172.18.0.1:7998:127.0.0.1:7998`
**When** de SSH-tunnel actief is
**Then** `curl http://172.18.0.1:7997/health` retourneert HTTP 200 (TEI)
**And** `curl http://172.18.0.1:7998/health` retourneert HTTP 200 (Infinity reranker)
**And** `curl http://172.18.0.1:8001/health` retourneert HTTP 200 (sparse)
**And** `curl http://172.18.0.1:8000/health` retourneert HTTP 200 (whisper)

---

## Module 2: Core-01 docker-compose.yml Update

### AC-CORE-001: Consumer-services gebruiken correcte endpoints

**Given** `deploy/docker-compose.yml` is bijgewerkt met de nieuwe environment variabelen
**When** de docker-compose configuratie wordt gevalideerd met `docker compose config`
**Then** de volgende environment variabelen zijn correct ingesteld:

| Service | Variable | Verwachte waarde |
|---------|----------|-----------------|
| `knowledge-ingest` | `TEI_URL` | `http://172.18.0.1:7997` |
| `knowledge-ingest` | `SPARSE_SIDECAR_URL` | `http://172.18.0.1:8001` |
| `retrieval-api` | `TEI_URL` | `http://172.18.0.1:7997` |
| `retrieval-api` | `TEI_RERANKER_URL` | `http://172.18.0.1:7998` |
| `retrieval-api` | `SPARSE_SIDECAR_URL` | `http://172.18.0.1:8001` |
| `scribe-api` | `WHISPER_SERVER_URL` | `http://172.18.0.1:8000` |
| `vexa-bot-manager` | `TRANSCRIBER_URL` | begint met `http://172.18.0.1:8000` |
| `librechat-klai` | `JINA_API_URL` | `http://172.18.0.1:7998/v1/rerank` |
| `research-api` | `TEI_URL` | `http://172.18.0.1:7997` |

### AC-CORE-002: Oude GPU service-definities verwijderd

**Given** `deploy/docker-compose.yml` is bijgewerkt
**When** het bestand wordt doorzocht op service-namen
**Then** de volgende services komen NIET voor als service-definitie:
- `tei:` (als top-level service key)
- `bge-m3-sparse:` (als top-level service key)
- `whisper-server:` (als top-level service key)
- `infinity-reranker:` (als top-level service key)

**Verificatie:**
```bash
grep -E '^\s{2}(tei|bge-m3-sparse|whisper-server|infinity-reranker):' deploy/docker-compose.yml
# Verwacht: geen output
```

### AC-CORE-003: depends_on referenties verwijderd

**Given** `deploy/docker-compose.yml` is bijgewerkt
**When** de `depends_on` secties van consumer-services worden geinspecteerd
**Then** `research-api.depends_on` bevat NIET `tei`
**And** `knowledge-ingest.depends_on` bevat NIET `tei` en NIET `bge-m3-sparse`
**And** `scribe-api.depends_on` bevat NIET `whisper-server`

### AC-CORE-004: Ongebruikte volumes verwijderd

**Given** `deploy/docker-compose.yml` is bijgewerkt
**When** de `volumes:` sectie wordt geinspecteerd
**Then** de volgende volumes komen NIET voor:
- `tei-models`
- `whisper-models`
- `infinity-models` (tenzij nog in gebruik door een andere service)

---

## Module 3: Python Config Defaults

### AC-PY-001: Config defaults verwijzen naar 172.18.0.1

**Given** de Python config-bestanden zijn bijgewerkt
**When** de default-waarden worden uitgelezen
**Then** de volgende bestanden bevatten de correcte defaults:

| Bestand | Veld | Verwachte default |
|---------|------|------------------|
| `klai-knowledge-ingest/knowledge_ingest/config.py` | `tei_url` | `http://172.18.0.1:7997` |
| `klai-knowledge-ingest/knowledge_ingest/config.py` | `sparse_sidecar_url` | `http://172.18.0.1:8001` |
| `klai-retrieval-api/retrieval_api/config.py` | `tei_url` | `http://172.18.0.1:7997` |
| `klai-retrieval-api/retrieval_api/config.py` | `tei_reranker_url` | `http://172.18.0.1:7998` |
| `klai-retrieval-api/retrieval_api/config.py` | `sparse_sidecar_url` | `http://172.18.0.1:8001` |
| `klai-scribe/scribe-api/app/core/config.py` | `whisper_server_url` | `http://172.18.0.1:8000` |

**Verificatie:**
```bash
grep -n "172.18.0.1" klai-knowledge-ingest/knowledge_ingest/config.py
grep -n "172.18.0.1" klai-retrieval-api/retrieval_api/config.py
grep -n "172.18.0.1" klai-scribe/scribe-api/app/core/config.py
# Verwacht: alle relevante regels tonen 172.18.0.1 adressen
```

### AC-PY-002: Geen referenties naar oude Docker service-namen

**Given** de Python config-bestanden zijn bijgewerkt
**When** de bestanden worden doorzocht op oude service-namen
**Then** de volgende strings komen NIET voor als default-waarden:
- `http://tei:8080`
- `http://bge-m3-sparse:8001`
- `http://whisper-server:8000`
- `http://tei-reranker:8080`
- `http://infinity-reranker:7997`

**Verificatie:**
```bash
grep -rn "tei:8080\|bge-m3-sparse:8001\|whisper-server:8000\|tei-reranker:8080\|infinity-reranker:7997" \
  klai-knowledge-ingest/knowledge_ingest/config.py \
  klai-retrieval-api/retrieval_api/config.py \
  klai-scribe/scribe-api/app/core/config.py
# Verwacht: geen output
```

---

## Module 4: Verificatie

### AC-VER-001: Alle gpu-01 endpoints bereikbaar via SSH-tunnel

**Given** alle wijzigingen uit Fase 1-3 zijn doorgevoerd
**And** de SSH-tunnel van core-01 naar gpu-01 is actief
**When** health checks worden uitgevoerd vanuit core-01
**Then** alle volgende endpoints retourneren HTTP 200:
- `http://172.18.0.1:7997/health` (TEI dense embeddings)
- `http://172.18.0.1:7998/health` (Infinity reranker)
- `http://172.18.0.1:8001/health` (BGE-M3 sparse)
- `http://172.18.0.1:8000/health` (Whisper STT)

### AC-VER-002: Test embedding-aanroep succesvol

**Given** TEI draait op poort 7997 met BAAI/bge-m3
**When** een embedding request wordt verstuurd:
```bash
curl -s -X POST http://172.18.0.1:7997/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input": "test embedding", "model": "BAAI/bge-m3"}'
```
**Then** de response bevat een `data` array met een `embedding` van dimensie 1024
**And** de HTTP status code is 200

### AC-VER-003: Test rerank-aanroep succesvol

**Given** Infinity draait op poort 7998 met BAAI/bge-reranker-v2-m3
**When** een rerank request wordt verstuurd:
```bash
curl -s -X POST http://172.18.0.1:7998/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"model": "BAAI/bge-reranker-v2-m3", "query": "test query", "documents": ["relevant document", "irrelevant document"]}'
```
**Then** de response bevat een `results` array met relevancy scores
**And** de HTTP status code is 200
**And** het eerste document scoort hoger dan het tweede

### AC-VER-004: Geen oude GPU-containers op core-01

**Given** alle wijzigingen zijn doorgevoerd en consumer-services zijn herstart
**When** de draaiende containers op core-01 worden geinspecteerd
**Then** de volgende containers bestaan NIET:
```bash
docker ps --format '{{.Names}}' | grep -E '(klai-core-tei|klai-core-bge-m3-sparse|klai-core-whisper-server|klai-core-infinity-reranker)'
# Verwacht: geen output
```
**And** `docker volume ls` toont geen actieve mounts voor `tei-models`, `whisper-models`, of `infinity-models`

---

## Definition of Done

De SPEC-DEVOPS-002 is voltooid wanneer:

1. **GPU-01 architectuur correct**: TEI op poort 7997 (embeddings), Infinity op poort 7998 (reranking), beide apart draaiend
2. **SSH-tunnel compleet**: Alle 4 poorten (7997, 7998, 8000, 8001) doorgestuurd naar 172.18.0.1 op core-01
3. **Core-01 consumers correct**: Alle 9 environment variabelen wijzen naar 172.18.0.1 endpoints
4. **Oude services verwijderd**: Geen GPU service-definities meer in core-01 docker-compose.yml
5. **Python defaults correct**: Alle 6 config defaults wijzen naar 172.18.0.1
6. **Latente bug gefixt**: `tei_reranker_url` in retrieval-api verwijst niet meer naar het niet-bestaande `tei-reranker:8080`
7. **Health checks groen**: Alle 4 inference endpoints bereikbaar via SSH-tunnel
8. **Functionele tests groen**: Embedding (1024 dims) en reranking scores werken end-to-end
9. **Geen oude containers**: Geen GPU-gerelateerde containers meer op core-01
10. **Consumer-logs schoon**: Geen connection errors in knowledge-ingest, retrieval-api, scribe-api, research-api
