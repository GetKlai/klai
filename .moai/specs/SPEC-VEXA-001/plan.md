# SPEC-VEXA-001: Implementatieplan v2 — Vexa Agentic-Runtime Migratie

> **Dit is plan-v2, geschreven na de mislukte eerste poging.**
> Lees sectie 7 van spec.md voor wat er fout ging.
> Dit plan vervangt plan.md voor de implementatie.

---

## Pre-implementatie checklist

**[HARD] Verifieer alles hieronder voordat je één regel code schrijft.**

| Item | Constraint | Verificatie |
|------|-----------|-------------|
| Image strategy | Build `meeting-api` en `runtime-api` van source — branch `feature/agentic-runtime`, commit `600cba04b9575c44cb74ee384c63b3fd7df98fe7` | `git ls-remote https://github.com/Vexa-ai/vexa feature/agentic-runtime` geeft dezelfde SHA |
| Bot image | `vexaai/vexa-bot:v20260315-2220` — publiek, gebruik as-is | `docker manifest inspect vexaai/vexa-bot:v20260315-2220` slaagt |
| Geen WhisperLive | meeting-api en runtime-api zijn Python FastAPI services — geen Supervisor, geen WhisperLive | Bevestigd door Dockerfiles en main.py |
| Port numbers | meeting-api: **8080** (niet 8056), runtime-api: **8090** | Bevestigd door Dockerfiles |
| DB variabelen | meeting-api gebruikt `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` — **niet** `DATABASE_URL` | Bevestigd door database.py — crasht bij startup als één ontbreekt |
| `ALLOW_PRIVATE_CALLBACKS` | **Verplicht `true`** op runtime-api — anders blokkeert het bot callbacks naar meeting-api (intern Docker IP) | Bevestigd door runtime_api/config.py |
| profiles.yaml | runtime-api vereist een `meeting` profile in profiles.yaml — volume mount verplicht | Runtime-api crasht niet, maar bots spawnen nooit zonder dit profile |
| Rollback eerst | Commit `8e04a81` moet gerevert worden **vóór** implementatie start | `git log --oneline` toont revert commit |

---

## 1. Rollback

Commit `8e04a81` introduceerde de fout (monolithische setup) en moet eerst gerevert worden:

```bash
git revert 8e04a81 --no-edit
```

---

## 2. Image strategie

### Publieke images — direct gebruiken

| Image | Tag | Status |
|-------|-----|--------|
| `vexaai/vexa-bot` | `v20260315-2220` | Publiek op Docker Hub — gepushed 2026-03-15, agentic-runtime era |
| `redis` | `7-alpine` | Standaard |

### Privaat — bouwen op core-01

`vexaai/meeting-api:dev` en `vexaai/runtime-api:dev` zijn privaat op Docker Hub. We bouwen ze lokaal op core-01.

**Lokale tags (niet `vexaai/` prefix — dat is de Docker Hub namespace):**
- `vexa-meeting-api:klai`
- `vexa-runtime-api:klai`

**Build op core-01:**

```bash
cd /tmp
git clone --depth=1 --branch feature/agentic-runtime https://github.com/Vexa-ai/vexa.git vexa-src
cd vexa-src
git checkout 600cba04b9575c44cb74ee384c63b3fd7df98fe7

# meeting-api: build context moet de repo root zijn (gebruikt libs/ subdirectory)
docker build -t vexa-meeting-api:klai -f services/meeting-api/Dockerfile .

# runtime-api: service directory als context
docker build -t vexa-runtime-api:klai -f services/runtime-api/Dockerfile services/runtime-api/

# Cleanup
rm -rf /tmp/vexa-src
```

---

## 3. Services die we deployen

| Service | Image | Port (intern) | RAM limit | Reden |
|---------|-------|--------------|-----------|-------|
| `vexa-meeting-api` | `vexa-meeting-api:klai` | 8080 | 512MB | Meeting CRUD, bot lifecycle, transcription collector |
| `vexa-runtime-api` | `vexa-runtime-api:klai` | 8090 | 256MB | Container orchestratie via Docker socket proxy |
| `vexa-redis` | `redis:7-alpine` | intern | 128MB | Bot state, pub/sub, transcription streams |
| `vexa-bot` (ephemeral) | `vexaai/vexa-bot:v20260315-2220` | — | 1.5GB per instance | Spawned door runtime-api via Docker socket |

---

## 4. Services die we NIET deployen

| Service | Reden |
|---------|-------|
| `vexaai/tts-service:dev` | TTS out of scope. `TTS_SERVICE_URL` leeg laten — meeting-api slaat het over als de env var niet gezet is. |
| MinIO | `STORAGE_BACKEND=local`, `RECORDING_ENABLED=false` — geen persistente audio opslag nodig. |
| `vexaai/api-gateway:dev` | portal-api praat rechtstreeks met meeting-api op `klai-net`. Geen gateway-laag nodig. |
| `vexaai/admin-api:dev` | Klai gebruikt Zitadel. Vexa's user management niet nodig. |
| `vexaai/transcription-service:dev` | Transcriptie gaat via bestaande whisper-server op gpu-01 via SSH tunnel. Migratie uitgesteld naar SPEC-VEXA-002. |

---

## 5. docker-compose.yml wijzigingen

### Verwijderen

Na rollback van `8e04a81`: de `vexa-meeting-api` block die `vexaai/vexa-lite:latest` gebruikt. De `vexa-runtime-api` block die hersteld wordt door de revert moet ook vervangen worden.

### Nieuw: `vexa-meeting-api`

```yaml
  vexa-meeting-api:
    image: vexa-meeting-api:klai
    restart: unless-stopped
    environment:
      DB_HOST: postgres
      DB_PORT: "5432"
      DB_NAME: vexa
      DB_USER: vexa
      DB_PASSWORD: ${VEXA_DB_PASSWORD}
      DB_SSL_MODE: disable
      REDIS_URL: redis://vexa-redis:6379/0
      API_KEYS: ${VEXA_API_KEY}
      ADMIN_TOKEN: ${VEXA_ADMIN_TOKEN}
      RUNTIME_API_URL: http://vexa-runtime-api:8090
      MEETING_API_URL: http://vexa-meeting-api:8080
      BOT_IMAGE_NAME: vexaai/vexa-bot:v20260315-2220
      TRANSCRIPTION_SERVICE_URL: http://172.18.0.1:8000/v1/audio/transcriptions
      TRANSCRIPTION_SERVICE_TOKEN: internal
      STORAGE_BACKEND: local
      RECORDING_ENABLED: "false"
      POST_MEETING_HOOKS: https://${DOMAIN}/api/bots/internal/webhook
      TRANSCRIPTION_COLLECTOR_URL: http://vexa-meeting-api:8080
    depends_on:
      postgres:
        condition: service_healthy
      vexa-redis:
        condition: service_healthy
      vexa-runtime-api:
        condition: service_started
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
    networks:
      - klai-net
      - net-postgres
      - net-redis
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

### Nieuw: `vexa-runtime-api`

```yaml
  vexa-runtime-api:
    image: vexa-runtime-api:klai
    restart: unless-stopped
    environment:
      REDIS_URL: redis://vexa-redis:6379/0
      ORCHESTRATOR_BACKEND: docker
      DOCKER_HOST: tcp://docker-socket-proxy:2375
      DOCKER_NETWORK: vexa-bots
      ALLOW_PRIVATE_CALLBACKS: "true"
      PROFILES_PATH: /app/profiles.yaml
      LOG_LEVEL: INFO
    volumes:
      - ./vexa/profiles.yaml:/app/profiles.yaml:ro
    depends_on:
      vexa-redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
    networks:
      - net-redis
      - socket-proxy
      - vexa-bots
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
```

### Nieuw: `vexa-redis`

```yaml
  vexa-redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    deploy:
      resources:
        limits:
          memory: 128M
    networks:
      - net-redis
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3
```

### Nieuw bestand: `deploy/vexa/profiles.yaml`

Runtime-api vereist dit bestand voor bot container templates. **Zonder dit bestand spawnt runtime-api nooit een bot.**

```yaml
profiles:
  meeting:
    image: "vexaai/vexa-bot:v20260315-2220"
    resources:
      memory_limit: "1536Mi"
      memory_request: "512Mi"
      cpu_limit: "2000m"
      cpu_request: "500m"
      shm_size: 1073741824   # 1GB shared memory — verplicht voor Chromium stabiliteit
    idle_timeout: 7200
    auto_remove: true
    env:
      LOG_LEVEL: "INFO"
    network_mode: "vexa-bots"
```

**Let op `shm_size`:** zonder 1GB shared memory crasht Chromium tijdens video rendering in Google Meet.

---

## 6. Environment variabelen — compleet overzicht

### meeting-api

| Variabele | Verplicht | Waarde | Bron |
|-----------|----------|--------|------|
| `DB_HOST` | **JA** | `postgres` | Hardcoded in compose |
| `DB_PORT` | **JA** | `5432` | Hardcoded |
| `DB_NAME` | **JA** | `vexa` | Hardcoded |
| `DB_USER` | **JA** | `vexa` | Hardcoded |
| `DB_PASSWORD` | **JA** | `${VEXA_DB_PASSWORD}` | SOPS |
| `REDIS_URL` | **JA** | `redis://vexa-redis:6379/0` | Hardcoded |
| `API_KEYS` | opt | `${VEXA_API_KEY}` | SOPS — portal-api's X-API-Key header |
| `ADMIN_TOKEN` | **JA** | `${VEXA_ADMIN_TOKEN}` | SOPS — nieuw, genereer met `openssl rand -hex 32` |
| `RUNTIME_API_URL` | opt | `http://vexa-runtime-api:8090` | Override default |
| `MEETING_API_URL` | opt | `http://vexa-meeting-api:8080` | Self-reference voor bot callbacks |
| `BOT_IMAGE_NAME` | opt | `vexaai/vexa-bot:v20260315-2220` | Override default |
| `TRANSCRIPTION_SERVICE_URL` | opt | `http://172.18.0.1:8000/v1/audio/transcriptions` | Doorgegeven aan bot |
| `STORAGE_BACKEND` | opt | `local` | Default is `minio`; **moet** worden overridden |
| `RECORDING_ENABLED` | opt | `"false"` | GDPR — geen persistente audio |
| `POST_MEETING_HOOKS` | opt | `https://${DOMAIN}/api/bots/internal/webhook` | portal-api webhook |
| `TTS_SERVICE_URL` | opt | (leeg laten) | TTS out of scope |

### runtime-api

| Variabele | Verplicht | Waarde | Bron |
|-----------|----------|--------|------|
| `REDIS_URL` | opt | `redis://vexa-redis:6379/0` | Override default |
| `DOCKER_HOST` | opt | `tcp://docker-socket-proxy:2375` | Override default (unix socket) |
| `DOCKER_NETWORK` | opt | `vexa-bots` | Bots hebben internet nodig |
| `ALLOW_PRIVATE_CALLBACKS` | **KRITISCH** | `"true"` | Zonder dit: bot callbacks geblokkeerd |
| `PROFILES_PATH` | opt | `/app/profiles.yaml` | Wijst naar volume mount |

### Nieuwe SOPS variabelen

| Variabele | Beschrijving |
|-----------|-------------|
| `VEXA_ADMIN_TOKEN` | Nieuw — JWT signing secret. Genereer: `openssl rand -hex 32` |
| `VEXA_DB_PASSWORD` | Controleer of het al in SOPS staat (was ooit handmatig aan `.env` toegevoegd) |
| `VEXA_API_KEY` | Controleer of het al in SOPS staat |

---

## 7. portal-api code wijzigingen

### config.py

```python
# Was:
vexa_meeting_api_url: str = "http://vexa-meeting-api:8056"
# Moet:
vexa_meeting_api_url: str = "http://vexa-meeting-api:8080"
```

### vexa.py

Auth header `X-API-Key` is al correct (commit `1dd7ea4`). Één change in `start_bot`:
```python
# Was:
json={"platform": platform, "native_meeting_id": native_meeting_id, "recording_enabled": True, ...}
# Moet:
json={"platform": platform, "native_meeting_id": native_meeting_id, "recording_enabled": False, ...}
```

### meetings.py — status mapping

| Oud | Nieuw |
|-----|-------|
| `"done"` | `"completed"` |
| `"processing"` | `"stopping"` |

Webhook payload format veranderd naar envelope:
```json
{"event": "meeting.completed", "data": {"meeting": {"id": ..., "platform": ..., "native_meeting_id": ..., "status": "completed"}}}
```

De `vexa_webhook()` handler moet dit envelope-formaat verwerken en `run_transcription` triggeren bij `status == "completed"`. Webhook authenticatie: geen HMAC signing in `POST_MEETING_HOOKS` — authenticeer via netwerk (intern Docker netwerk, alleen vexa-meeting-api kan dit endpoint bereiken).

### bot_poller.py

```python
# Status check: "done" → "completed"
if meeting.status == "completed":  # was "done"
```

---

## 8. Verificatiestappen

Voer in deze volgorde uit na deployment:

```bash
# 1. Redis
docker exec klai-core-vexa-redis-1 redis-cli ping
# Verwacht: PONG

# 2. Runtime-api health
docker exec klai-core-portal-api-1 curl -s http://vexa-runtime-api:8090/health
# Verwacht: {"status":"ok"}

# 3. Meeting-api health
docker exec klai-core-portal-api-1 curl -s http://vexa-meeting-api:8080/health
# Verwacht: {"status":"ok"}

# 4. Logs na startup — geen crashes
docker logs klai-core-vexa-meeting-api-1 --tail 30
# Verwacht: database init + redis connect, geen errors

# 5. Auth werkt
VEXA_API_KEY=$(docker exec klai-core-portal-api-1 printenv VEXA_API_KEY)
docker exec klai-core-portal-api-1 curl -s \
  -H "X-API-Key: $VEXA_API_KEY" \
  http://vexa-meeting-api:8080/bots
# Verwacht: JSON array (mag leeg zijn)

# 6. Runtime-api profiles geladen
docker exec klai-core-portal-api-1 curl -s http://vexa-runtime-api:8090/profiles
# Verwacht: JSON met "meeting" profile — als dat ontbreekt: profiles.yaml volume mount mislukt

# 7. End-to-end bot start
docker exec klai-core-portal-api-1 curl -s -X POST \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"platform":"google_meet","native_meeting_id":"test-xxx-yyy","bot_name":"Klai"}' \
  http://vexa-meeting-api:8080/bots
# Verwacht: 201, status "requested" of "joining"

# 8. Bot container gespawnd
docker ps | grep vexa-bot
# Verwacht: 1 container zichtbaar

# 9. Bot op juist netwerk
docker inspect <container_name> | grep vexa-bots
# Verwacht: vexa-bots network aanwezig

# 10. Geen actieve bots na stoppen
docker ps | grep vexa-bot
# Verwacht: leeg (auto_remove=true)
```

---

## 9. Wat NIET aanraken

- `VexaClient` auth header `X-API-Key` — correct (commit `1dd7ea4`)
- `parse_meeting_url()` in `vexa.py` — correct
- `docker-socket-proxy` — `EXEC: 1` al verwijderd
- `vexa` PostgreSQL database op core-01 — bestaat al
- whisper-server op gpu-01 — ongewijzigd
- IMAP/calendar invite pipeline — ongewijzigd
