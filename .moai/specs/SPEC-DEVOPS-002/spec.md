---
id: SPEC-DEVOPS-002
version: 1.0.0
status: completed
created: 2026-03-30
updated: 2026-03-30
author: MoAI
priority: high
---

# SPEC-DEVOPS-002: GPU-01 Inference Migration Fix -- TEI + Infinity Split Herstellen

## HISTORY

| Versie | Datum      | Auteur | Wijziging                |
|--------|------------|--------|--------------------------|
| 1.0.0  | 2026-03-30 | MoAI   | Initieel SPEC-document   |

---

## Samenvatting

Tijdens de uitvoering van SPEC-GPU-001 is een ongeautoriseerde architectuurwijziging doorgevoerd: TEI (text-embeddings-inference) en de aparte infinity-reranker zijn samengevoegd tot een enkele Infinity-instantie op gpu-01. Dit is architectureel onjuist en brengt productierisico's met zich mee (bekende GPU memory leak in Infinity, issue #517). Deze SPEC herstelt de correcte split-architectuur en repareert alle verbroken service-referenties op core-01.

---

## Module 1: GPU-01 Service Herconfiguratie

### Omgeving

- gpu-01 (Hetzner GEX44, RTX 4000 Ada 20GB GDDR6, IP 5.9.10.215, FSN1-DC13)
- Docker Compose configuratie: `/opt/klai-gpu/docker-compose.yml` op gpu-01
- Huidige (foutieve) situatie: enkele Infinity-instantie op poort 7997 bedient zowel bge-m3 dense embeddings als bge-reranker-v2-m3 reranking
- bge-m3-sparse op poort 8001 en whisper-server op poort 8000 zijn correct en worden niet gewijzigd
- SSH-tunnel van core-01 naar gpu-01 bindt inference-poorten aan 172.18.0.1 (Docker host gateway op core-01)

### Aannames

- **ASM-001**: TEI Docker image `ghcr.io/huggingface/text-embeddings-inference:1.5` ondersteunt BAAI/bge-m3 correct (bevestigd door model card)
- **ASM-002**: TEI ondersteunt het `/v1/embeddings` OpenAI-compatible endpoint, waardoor geen code-aanpassing in embedder.py nodig is (reeds gevalideerd in commit 235a259)
- **ASM-003**: Infinity heeft een bekende GPU memory leak (GitHub issue #517, open sinds jan 2025) die het ongeschikt maakt als sole embedding service onder productielast
- **ASM-004**: De SSH-tunnel op core-01 kan uitgebreid worden met een extra poortforwarding (7998) zonder impact op bestaande tunnels
- **ASM-005**: gpu-01 heeft voldoende VRAM (20GB) voor TEI (bge-m3) + Infinity (bge-reranker-v2-m3) + bge-m3-sparse + whisper-server naast elkaar

### Requirements

**REQ-GPU-001** (Event-Driven):
WHEN de gpu-01 Docker Compose stack wordt gestart, THEN SHALL de `tei` service opstarten op poort 7997 met het model BAAI/bge-m3 voor dense embeddings, gebruikmakend van het Docker image `ghcr.io/huggingface/text-embeddings-inference:1.5`.

**REQ-GPU-002** (Event-Driven):
WHEN de gpu-01 Docker Compose stack wordt gestart, THEN SHALL de `infinity` service opstarten op poort 7998 met uitsluitend het model BAAI/bge-reranker-v2-m3 voor reranking.

**REQ-GPU-003** (Ubiquitous):
De services `bge-m3-sparse` (poort 8001) en `whisper-server` (poort 8000) op gpu-01 SHALL ongewijzigd blijven.

**REQ-GPU-004** (Event-Driven):
WHEN de SSH-tunnel van core-01 naar gpu-01 wordt opgezet, THEN SHALL poort 7998 (Infinity reranker) worden doorgestuurd via `/etc/systemd/system/gpu-tunnel.service` op core-01, naast de bestaande poorten 7997, 8000 en 8001, gebonden aan 172.18.0.1 op core-01. De service wordt herladen via `systemctl daemon-reload && systemctl restart gpu-tunnel.service`.

### Specificaties

| Service | Poort | Model | Docker Image | Functie |
|---------|-------|-------|-------------|---------|
| `tei` | 7997 | BAAI/bge-m3 | `ghcr.io/huggingface/text-embeddings-inference:1.5` | Dense embeddings |
| `infinity` | 7998 | BAAI/bge-reranker-v2-m3 | `michaelf34/infinity:latest` | Reranking |
| `bge-m3-sparse` | 8001 | BAAI/bge-m3 | custom | Sparse embeddings (ongewijzigd) |
| `whisper-server` | 8000 | large-v3 | custom | STT (ongewijzigd) |

---

## Module 2: Core-01 docker-compose.yml Update

### Omgeving

- core-01 Docker Compose: `deploy/docker-compose.yml` in deze repo, deployed naar `/opt/klai/docker-compose.yml`
- 6 consumer-services refereren nog naar oude Docker service-namen (intra-compose netwerk) in plaats van naar de SSH-tunnel endpoints op 172.18.0.1
- 4 oude GPU service-definities staan nog in docker-compose.yml en moeten verwijderd worden
- Bijbehorende `depends_on` entries en volumes zijn stale

### Aannames

- **ASM-006**: De 172.18.0.1 adressen zijn bereikbaar vanuit alle Docker-containers op core-01 via het Docker host gateway
- **ASM-007**: Environment variabelen in docker-compose.yml overschrijven Python config defaults (env vars hebben voorrang)
- **ASM-008**: Verwijdering van de oude service-definities heeft geen neveneffecten op andere services

### Requirements

**REQ-CORE-001** (Event-Driven):
WHEN de core-01 Docker Compose stack wordt gestart, THEN SHALL elke consumer-service de correcte inference endpoints gebruiken via 172.18.0.1, conform de volgende mapping:

| Service | Variable | Nieuwe waarde |
|---------|----------|---------------|
| `knowledge-ingest` | `TEI_URL` | `http://172.18.0.1:7997` |
| `knowledge-ingest` | `SPARSE_SIDECAR_URL` | `http://172.18.0.1:8001` |
| `retrieval-api` | `TEI_URL` | `http://172.18.0.1:7997` |
| `retrieval-api` | `TEI_RERANKER_URL` | `http://172.18.0.1:7998` |
| `retrieval-api` | `SPARSE_SIDECAR_URL` | `http://172.18.0.1:8001` |
| `scribe-api` | `WHISPER_SERVER_URL` | `http://172.18.0.1:8000` |
| `vexa-bot-manager` | `TRANSCRIBER_URL` | `http://172.18.0.1:8000/...` |
| `librechat-klai` | `JINA_API_URL` | `http://172.18.0.1:7998/v1/rerank` |
| `research-api` | `TEI_URL` | `http://172.18.0.1:7997` |

**REQ-CORE-002** (Unwanted):
De docker-compose.yml SHALL NIET de volgende service-definities bevatten na de wijziging: `tei`, `bge-m3-sparse`, `whisper-server`, `infinity-reranker`. Deze services draaien nu op gpu-01.

**REQ-CORE-003** (Event-Driven):
WHEN de oude GPU service-definities worden verwijderd, THEN SHALL alle bijbehorende `depends_on` referenties ook worden verwijderd:
- `research-api`: verwijder `- tei`
- `knowledge-ingest`: verwijder `- tei` en `- bge-m3-sparse`
- `scribe-api`: verwijder `- whisper-server`

**REQ-CORE-004** (Event-Driven):
WHEN de oude GPU service-definities worden verwijderd, THEN SHALL de bijbehorende ongebruikte volumes worden verwijderd: `tei-models`, `whisper-models`, en `infinity-models` (indien niet meer in gebruik).

---

## Module 3: Python Config Defaults

### Omgeving

- 4 Python config-bestanden bevatten hardcoded defaults die verwijzen naar oude Docker service-namen
- Deze defaults worden overschreven door environment variabelen maar vormen een risico als de env vars ontbreken
- Er is een latente bug: `retrieval-api` config verwijst naar `tei-reranker:8080` dat nooit bestond

### Aannames

- **ASM-009**: De Python config defaults worden alleen als fallback gebruikt; de docker-compose environment variabelen hebben voorrang
- **ASM-010**: Het updaten van de defaults naar 172.18.0.1 adressen is veilig voor zowel productie als lokale ontwikkeling (lokale dev kan alsnog overschrijven via env vars)

### Requirements

**REQ-PY-001** (Ubiquitous):
De Python config defaults SHALL de correcte 172.18.0.1 adressen bevatten als fallback-waarden:

| Bestand | Veld | Huidige waarde (fout) | Nieuwe waarde |
|---------|------|----------------------|---------------|
| `klai-knowledge-ingest/knowledge_ingest/config.py:8` | `tei_url` | `http://tei:8080` | `http://172.18.0.1:7997` |
| `klai-knowledge-ingest/knowledge_ingest/config.py:33` | `sparse_sidecar_url` | `http://bge-m3-sparse:8001` | `http://172.18.0.1:8001` |
| `klai-retrieval-api/retrieval_api/config.py:12` | `tei_url` | `http://tei:8080` | `http://172.18.0.1:7997` |
| `klai-retrieval-api/retrieval_api/config.py:13` | `tei_reranker_url` | `http://tei-reranker:8080` | `http://172.18.0.1:7998` |
| `klai-retrieval-api/retrieval_api/config.py:23` | `sparse_sidecar_url` | `""` | `http://172.18.0.1:8001` |
| `klai-scribe/scribe-api/app/core/config.py:18` | `whisper_server_url` | `http://whisper-server:8000` | `http://172.18.0.1:8000` |

**REQ-PY-002** (Unwanted):
De Python config defaults SHALL NIET verwijzen naar Docker service-namen (`tei`, `bge-m3-sparse`, `whisper-server`, `tei-reranker`, `infinity-reranker`) die niet meer bestaan op core-01.

---

## Module 4: Verificatie

### Omgeving

- Na alle wijzigingen moeten alle inference-paden end-to-end gevalideerd worden
- Health checks moeten via de SSH-tunnel lopen (172.18.0.1 op core-01)
- Oude GPU-containers op core-01 mogen niet meer draaien

### Requirements

**REQ-VER-001** (Event-Driven):
WHEN alle wijzigingen zijn doorgevoerd, THEN SHALL elk gpu-01 service-endpoint bereikbaar zijn via de SSH-tunnel:
- TEI health: `http://172.18.0.1:7997/health`
- Infinity health: `http://172.18.0.1:7998/health`
- BGE-M3 sparse health: `http://172.18.0.1:8001/health`
- Whisper health: `http://172.18.0.1:8000/health`

**REQ-VER-002** (Event-Driven):
WHEN de TEI service draait, THEN SHALL een test embedding-aanroep via `knowledge-ingest` een geldig dense embedding-vector opleveren (dimensie 1024 voor bge-m3).

**REQ-VER-003** (Event-Driven):
WHEN de Infinity service draait op poort 7998, THEN SHALL een test rerank-aanroep via `retrieval-api` een geldige reranking-score opleveren.

**REQ-VER-004** (Unwanted):
Er SHALL GEEN oude GPU-gerelateerde containers (tei, bge-m3-sparse, whisper-server, infinity-reranker) meer draaien op core-01 na de migratie.

---

## Beperkingen

- **CON-001**: De migratie moet in een specifieke veilige volgorde worden uitgevoerd (gpu-01 eerst, daarna core-01 consumers) om downtime te minimaliseren
- **CON-002**: Elke stap moet een rollback-procedure hebben
- **CON-003**: Alle service health checks moeten slagen voordat de migratie als voltooid wordt verklaard
- **CON-004**: Volg `.claude/rules/klai/container-preflight.md` voor alle docker compose operaties
- **CON-005**: Volg `.claude/rules/klai/pitfalls/infrastructure.md` voor environment variabele wijzigingen
- **CON-006**: Gebruik GEEN `sed` of `echo` om `/opt/klai/.env` te wijzigen -- env wijzigingen gaan in docker-compose.yml
- **CON-007**: embedder.py (commit 235a259) is reeds compatible met TEI's `/v1/embeddings` endpoint -- geen revert nodig

---

## Traceerbaarheid

| Requirement | Plan Referentie | Acceptatie Referentie |
|-------------|----------------|----------------------|
| REQ-GPU-001 | Fase 1, Stap 1 | AC-GPU-001 |
| REQ-GPU-002 | Fase 1, Stap 2 | AC-GPU-002 |
| REQ-GPU-003 | Fase 1 (geen actie) | AC-GPU-003 |
| REQ-GPU-004 | Fase 1, Stap 3 | AC-GPU-004 |
| REQ-CORE-001 | Fase 2, Stap 1 | AC-CORE-001 |
| REQ-CORE-002 | Fase 2, Stap 2 | AC-CORE-002 |
| REQ-CORE-003 | Fase 2, Stap 3 | AC-CORE-003 |
| REQ-CORE-004 | Fase 2, Stap 4 | AC-CORE-004 |
| REQ-PY-001 | Fase 3, Stap 1 | AC-PY-001 |
| REQ-PY-002 | Fase 3 (impliciete verificatie) | AC-PY-002 |
| REQ-VER-001 | Fase 4, Stap 1 | AC-VER-001 |
| REQ-VER-002 | Fase 4, Stap 2 | AC-VER-002 |
| REQ-VER-003 | Fase 4, Stap 3 | AC-VER-003 |
| REQ-VER-004 | Fase 4, Stap 4 | AC-VER-004 |
