# SPEC-VEXA-003: Implementation Plan — Clean-Slate Rebuild on Upstream Vexa main

> Phase ordering uses priority labels (High / Medium / Low) — no time estimates.
> Every phase ends with a mechanical verification step before the next begins.

---

## Pre-flight checklist (must pass before Phase 1 starts)

| Item | Constraint | How to verify |
|------|-----------|---------------|
| Upstream main pinned | Resolve exact commit SHA for `upstream/main` head | `cd vexa && git rev-parse upstream/main` |
| Fork remote present | `origin = mvletter/vexa`, `upstream = Vexa-ai/vexa` | `git remote -v` |
| SPEC-VEXA-001 post-mortem read | §7 of VEXA-001 spec.md re-read; pitfalls list understood | Written confirmation in phase notes |
| Scribe snapshot captured | `reports/scribe-snapshot-pre.txt` generated | See acceptance.md §B.2 |
| Scribe volume snapshot captured | `FILE_COUNT_BEFORE`, `BYTES_BEFORE` recorded | See acceptance.md §B.1 |
| Vexa DB data-loss authorised | User-confirmed in conversation | Quote in phase notes |
| Current vexa image backup saved | `docker save` of `vexa-meeting-api:klai` + `vexa-runtime-api:klai` to `/backup/vexa-rollback/` | `ls -la /backup/vexa-rollback/` |
| SOPS access verified | `~/.config/sops/age/keys.txt` present | `sops -d /opt/klai/.env >/dev/null` |

---

## Phase 1 (Priority High) — Prepare the fork

Goal: A buildable `klai/main-YYMMDD-<sha>` branch containing upstream main + any
required bot fixes.

### 1.1 Create branch

```bash
cd vexa
git fetch upstream
git fetch origin
UPSTREAM_SHA=$(git rev-parse upstream/main)
BRANCH="klai/main-$(date +%y%m%d)-${UPSTREAM_SHA:0:7}"
git checkout -b "$BRANCH" upstream/main
```

### 1.2 Assess bot fixes against upstream

For each fix, perform a targeted code review on upstream main before cherry-picking:

**`fdb751f` — participant registry MutationObserver**

```bash
# Find the current upstream file responsible for participant counting
git -C vexa grep -l "MutationObserver\|participant" upstream/main -- 'services/vexa-bot/'
# Diff the original fork patch against upstream target file
git -C vexa show fdb751f -- services/vexa-bot/
```

Decision matrix:
- Upstream already uses MutationObserver registry → SKIP, record in phase notes
- Upstream has a refactored but still text-scan based counter → port semantically
- Conflict-free cherry-pick possible → `git cherry-pick fdb751f`

**`787e517` — remove track.stop() and transceiver renegotiation**

```bash
git -C vexa show 787e517 -- services/vexa-bot/
git -C vexa grep -nE 'track\.stop\(\)|renegotiat' upstream/main -- 'services/vexa-bot/'
```

Same decision matrix as above. If the upstream "disable-incoming-video" feature branch
has merged and the problematic code is gone, SKIP.

### 1.3 Cherry-pick or port

For each fix kept:

```bash
git cherry-pick <sha>   # attempt first
# On conflict: edit files manually, commit with message
#   "fix(vexa-bot): <title> (ported from <sha> onto upstream main)"
```

### 1.4 Push branch

```bash
git push origin "$BRANCH"
```

**Exit gate**: `git log --oneline $BRANCH ^upstream/main` returns 0–2 commits, each
with a clear message. `git rev-parse $BRANCH` captured for deploy/VERSIONS.md.

---

## Phase 2 (Priority High) — Finalise deployment decisions

Goal: Written record of each decision from research.md §5, with rationale.

Decisions to confirm in a decisions log (`.moai/specs/SPEC-VEXA-003/decisions.md` OR
added to this plan as an appendix):

1. **Storage**: Reuse Garage (`MINIO_ENDPOINT=garage:3900`, bucket `vexa-recordings`).
   Create bucket via `mc mb` before Phase 6
2. **Postgres**: Share klai postgres (pg18). `vexa` database wiped and recreated in
   Phase 6
3. **Redis**: Keep `redis:8-alpine` (forward-compat policy per user). Phase 7.X
   executes compatibility tests; if they fail, rollback is a single-line compose
   change to `redis:7.0-alpine`
4. **Optional services**: `mcp`, `dashboard`, `tts-service` **NOT** shipped in v1
5. **Scribe transcription path**: `tier=deferred` form field + 503/Retry-After retry
6. **Webhook HMAC**: Deferred — keep source-IP allowlist + optional Bearer
7. **Port retention on gpu-01**: Host `:8000` → container `:80` (nginx LB)

**Exit gate**: Decisions documented. Any reversal requires updating this plan before
Phase 3 starts.

---

## Phase 3 (Priority High) — Update Klai deploy manifests

Goal: `deploy/docker-compose.yml`, `deploy/docker-compose.gpu.yml`, and
`deploy/vexa/profiles.yaml` updated to match §3 of spec.md. No service started yet.

### 3.1 Remove old vexa blocks

Delete from `deploy/docker-compose.yml`:
- `vexa-meeting-api:` block (lines ~710–755)
- `vexa-runtime-api:` block (lines ~757–791)
- Keep `vexa-redis:` block (image stays `redis:8-alpine`, resources raised to 256M)

### 3.2 Add new service blocks

Add to `deploy/docker-compose.yml` (pin to actual build tag from Phase 6):

```yaml
  # ─── Vexa admin-api ──────────────────────────────────────────────────────
  admin-api:
    image: vexaai/admin-api:0.10.0-YYMMDD-HHMM   # pinned at Phase 6
    restart: unless-stopped
    environment:
      DB_HOST: postgres
      DB_PORT: "5432"
      DB_NAME: vexa
      DB_USER: vexa
      DB_PASSWORD: ${VEXA_DB_PASSWORD}
      DB_SSL_MODE: disable
      ADMIN_API_TOKEN: ${VEXA_ADMIN_TOKEN}
      INTERNAL_API_SECRET: ${INTERNAL_API_SECRET}
      LOG_LEVEL: INFO
    init: true
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - klai-net
      - net-postgres
    deploy:
      resources:
        limits:
          memory: 256M
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

  # ─── Vexa api-gateway ────────────────────────────────────────────────────
  api-gateway:
    image: vexaai/api-gateway:0.10.0-YYMMDD-HHMM
    restart: unless-stopped
    environment:
      ADMIN_API_URL: http://admin-api:8001
      MEETING_API_URL: http://meeting-api:8080
      TRANSCRIPTION_COLLECTOR_URL: http://meeting-api:8080
      MCP_URL: http://mcp:18888            # harmless if mcp not deployed
      CALENDAR_SERVICE_URL: http://calendar-service:8050   # unused
      AGENT_API_URL: http://agent-api:8100 # unused
      REDIS_URL: redis://:${VEXA_REDIS_PASSWORD}@vexa-redis:6379/0
      INTERNAL_API_SECRET: ${INTERNAL_API_SECRET}
      RATE_LIMIT_RPM: "120"
      LOG_LEVEL: INFO
      CORS_ORIGINS: "*"
    init: true
    depends_on:
      admin-api:
        condition: service_healthy
      meeting-api:
        condition: service_started
    networks:
      - klai-net
      - net-redis
    deploy:
      resources:
        limits:
          memory: 1G

  # ─── Vexa meeting-api ────────────────────────────────────────────────────
  meeting-api:
    image: vexaai/meeting-api:0.10.0-YYMMDD-HHMM
    restart: unless-stopped
    environment:
      DB_HOST: postgres
      DB_PORT: "5432"
      DB_NAME: vexa
      DB_USER: vexa
      DB_PASSWORD: ${VEXA_DB_PASSWORD}
      DB_SSL_MODE: disable
      REDIS_URL: redis://:${VEXA_REDIS_PASSWORD}@vexa-redis:6379/0
      REDIS_HOST: vexa-redis
      REDIS_PORT: "6379"
      ADMIN_TOKEN: ${VEXA_ADMIN_TOKEN}
      RUNTIME_API_URL: http://runtime-api:8090
      RUNTIME_API_TOKEN: ${BOT_API_TOKEN}
      MEETING_API_URL: http://meeting-api:8080
      BOT_IMAGE_NAME: vexaai/vexa-bot:0.10.0-YYMMDD-HHMM
      DOCKER_NETWORK: vexa-bots
      TRANSCRIPTION_COLLECTOR_URL: http://meeting-api:8080
      STORAGE_BACKEND: minio          # Garage speaks S3; MINIO_* vars point at it
      MINIO_ENDPOINT: garage:3900
      MINIO_ACCESS_KEY: ${GARAGE_ACCESS_KEY}
      MINIO_SECRET_KEY: ${GARAGE_SECRET_KEY}
      MINIO_BUCKET: vexa-recordings
      MINIO_SECURE: "false"
      RECORDING_ENABLED: "false"      # GDPR
      CAPTURE_MODES: "audio"
      TRANSCRIPTION_SERVICE_URL: http://172.18.0.1:8000/v1/audio/transcriptions
      TRANSCRIPTION_SERVICE_TOKEN: internal
      POST_MEETING_HOOKS: http://portal-api:8010/api/bots/internal/webhook
      LOG_LEVEL: INFO
    init: true
    depends_on:
      postgres:
        condition: service_healthy
      vexa-redis:
        condition: service_healthy
      runtime-api:
        condition: service_started
    networks:
      klai-net:
        aliases:
          - vexa-meeting-api   # backwards-compat alias — portal-api config unchanged
      net-postgres: {}
      vexa-bots: {}
    deploy:
      resources:
        limits:
          memory: 1G
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  # ─── Vexa runtime-api ────────────────────────────────────────────────────
  runtime-api:
    image: vexaai/runtime-api:0.10.0-YYMMDD-HHMM
    restart: unless-stopped
    environment:
      DOCKER_NETWORK: vexa-bots
      BROWSER_IMAGE: vexaai/vexa-bot:0.10.0-YYMMDD-HHMM
      AGENT_IMAGE: ""                 # agent profile not used
      REDIS_URL: redis://:${VEXA_REDIS_PASSWORD}@vexa-redis:6379/0
      MINIO_ENDPOINT: garage:3900
      MINIO_ACCESS_KEY: ${GARAGE_ACCESS_KEY}
      MINIO_SECRET_KEY: ${GARAGE_SECRET_KEY}
      MINIO_BUCKET: vexa-recordings
      BOT_API_TOKEN: ${BOT_API_TOKEN}
      API_KEYS: ${BOT_API_TOKEN}
      DB_HOST: postgres
      DB_PORT: "5432"
      DB_NAME: vexa
      DB_USER: vexa
      DB_PASSWORD: ${VEXA_DB_PASSWORD}
      DB_SSL_MODE: disable
      PROFILES_PATH: /app/profiles.yaml
      ALLOW_PRIVATE_CALLBACKS: "1"
      LOG_LEVEL: INFO
    group_add:
      - "988"   # docker GID on core-01
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./vexa/profiles.yaml:/app/profiles.yaml:ro
    init: true
    depends_on:
      vexa-redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    networks:
      - vexa-bots
      - net-postgres
    deploy:
      resources:
        limits:
          memory: 256M
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # ─── Vexa Redis (bot state, pub/sub) — redis:8-alpine (forward-compat) ───
  # Per REQ-U-006: we do not downgrade to 7.0 unless Phase 7 compat tests fail.
  # Rollback: flip to image: redis:7.0-alpine and `docker compose up -d vexa-redis`.
  vexa-redis:
    image: redis:8-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes", "--requirepass", "${VEXA_REDIS_PASSWORD}", "--appendfsync", "everysec"]
    deploy:
      resources:
        limits:
          memory: 256M
    networks:
      - vexa-bots
      - net-redis
    volumes:
      - vexa-redis-data:/data
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a $VEXA_REDIS_PASSWORD --no-auth-warning ping | grep PONG"]
      interval: 10s
      timeout: 5s
      retries: 3
```

Declare new volume at file top:

```yaml
volumes:
  vexa-redis-data:
```

### 3.3 Update `deploy/vexa/profiles.yaml`

Rewrite to match upstream schema (see spec.md §3.3):

```yaml
profiles:
  meeting:
    image: "${BROWSER_IMAGE}"
    command: ["/app/vexa-bot/entrypoint.sh"]
    working_dir: "/app/vexa-bot"
    resources:
      cpu_request: "1000m"
      cpu_limit: "1500m"
      memory_request: "1100Mi"
      memory_limit: "1536Mi"
      shm_size: 2147483648    # 2 GiB for Chrome stability
    idle_timeout: 0           # meeting-api scheduler owns lifetime
    auto_remove: false
    env:
      DISPLAY: "${DISPLAY:-:99}"
      NODE_ENV: "production"
      REDIS_URL: "${REDIS_URL}"
      TRANSCRIPTION_SERVICE_URL: "${TRANSCRIPTION_SERVICE_URL}"
      TRANSCRIPTION_SERVICE_TOKEN: "${TRANSCRIPTION_SERVICE_TOKEN}"
    ports:
      "9222/tcp": {}
```

`browser-session` and `agent` profiles can be omitted — Klai does not use them.

### 3.4 Update `deploy/docker-compose.gpu.yml`

Remove:

```yaml
whisper-server:
  image: ghcr.io/getklai/whisper-server:latest
  ...
```

Add:

```yaml
  transcription-worker-1:
    image: vexaai/transcription-service:0.10.0-YYMMDD-HHMM
    restart: unless-stopped
    environment:
      WORKER_ID: "1"
      MODEL_SIZE: large-v3-turbo
      DEVICE: cuda
      COMPUTE_TYPE: int8
      MAX_ACTIVE_REQUESTS: "20"
      REALTIME_RESERVED_SLOTS: "1"
      FAIL_FAST_WHEN_BUSY: "true"
      BUSY_RETRY_AFTER_S: "1"
    volumes:
      - /opt/klai/vexa-transcription-models:/app/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    networks:
      - transcription-net

  transcription-worker-2:
    # identical to worker-1 with WORKER_ID: "2"

  transcription-api:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:80"   # retains existing consumer URL
    volumes:
      - /opt/klai/vexa-transcription/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - transcription-worker-1
      - transcription-worker-2
    networks:
      - transcription-net
```

Declare network:

```yaml
networks:
  transcription-net:
    driver: bridge
```

### 3.5 Add pre-commit image-tag check

Create `deploy/check-image-tags.sh`:

```bash
#!/bin/sh
set -e
MATCHES=$(grep -nE 'vexaai/[a-z-]+:(latest|dev|staging)\b' \
  deploy/docker-compose.yml deploy/docker-compose.gpu.yml 2>/dev/null || true)
if [ -n "$MATCHES" ]; then
  echo "ERROR: Vexa service uses mutable tag. See REQ-U-002 in SPEC-VEXA-003." >&2
  echo "$MATCHES" >&2
  exit 1
fi
# Optional: also enforce the full YYMMDD-HHMM pattern
if grep -nE 'vexaai/[a-z-]+:[^[:space:]]+' deploy/docker-compose.yml \
   | grep -vE 'vexaai/[a-z-]+:[0-9]+\.[0-9]+\.[0-9]+-[0-9]{6}-[0-9]{4}' >/dev/null; then
  echo "ERROR: vexa image tag does not match <version>-YYMMDD-HHMM." >&2
  exit 1
fi
echo "OK: all Vexa image tags are pinned to an immutable timestamp."
```

Make executable and wire into `.githooks/pre-commit` (or copy to klai-infra's
pre-commit hook). Test locally:

```bash
chmod +x deploy/check-image-tags.sh
./deploy/check-image-tags.sh || echo "expected to fail until Phase 6 writes real tags"
```

### 3.6 SOPS-encrypt new env vars

```bash
sops -d /opt/klai/.env > /tmp/env.plain
cat >> /tmp/env.plain <<EOF
INTERNAL_API_SECRET=$(openssl rand -hex 32)
BOT_API_TOKEN=$(openssl rand -hex 32)
EOF
# Only if VEXA_ADMIN_TOKEN is missing:
grep -q '^VEXA_ADMIN_TOKEN=' /tmp/env.plain || \
  echo "VEXA_ADMIN_TOKEN=$(openssl rand -hex 32)" >> /tmp/env.plain
sops --encrypt /tmp/env.plain > /opt/klai/.env.new
mv /opt/klai/.env.new /opt/klai/.env
shred -u /tmp/env.plain
```

Follow `follow-loaded-procedures` pitfall — use SOPS decrypt → modify → encrypt-in-place →
`mv`, no ad-hoc redirects.

**Exit gate**:
- `./deploy/check-image-tags.sh` exits non-zero with clear message (it should fail on
  placeholder `YYMMDD-HHMM` until Phase 6 writes real tags) — script logic verified
- `docker compose -f deploy/docker-compose.yml config --quiet` passes (yaml valid,
  env refs resolve)
- `sops -d /opt/klai/.env | grep -E '^(INTERNAL_API_SECRET|BOT_API_TOKEN)=' | wc -l`
  returns 2

---

## Phase 4 (Priority High) — Portal-api changes

Goal: portal-api works against new meeting-api. Minimal code changes — mostly env and
config validation.

### 4.1 Verify `vexa_meeting_api_url`

Already `http://vexa-meeting-api:8080` in config. New service name is `meeting-api`.

**Decision**: add a compose network alias so `vexa-meeting-api` resolves to `meeting-api`
on `klai-net` (see Phase 3.2 meeting-api block). This minimises portal-api code churn.

### 4.2 Confirm webhook handler supports envelope

`VexaWebhookPayload._normalize` already handles the envelope shape. Add a unit test in
portal-api with the upstream envelope fixture (from research.md §3.5) to prevent
regression:

```python
# klai-portal/backend/tests/test_vexa_webhook.py — new file or extend
def test_webhook_accepts_upstream_envelope():
    payload = {
        "event_id": "evt_abc",
        "event_type": "meeting.completed",
        "api_version": "2026-03-01",
        "created_at": "2026-04-19T10:00:00+00:00",
        "data": {"meeting": {"id": 1, "platform": "google_meet",
                              "native_meeting_id": "abc-def-ghi", "status": "completed",
                              "end_time": "2026-04-19T10:05:00+00:00"}},
    }
    model = VexaWebhookPayload.model_validate(payload)
    assert model.platform == "google_meet"
    assert model.native_meeting_id == "abc-def-ghi"
    assert model.status == "completed"
    assert model.vexa_meeting_id == 1
```

### 4.3 Product events — no change expected

`emit_event("meeting.completed", ...)` remains driven by portal-api's own state machine.
No schema change to `product_events`.

### 4.4 No dashboard adoption → no new UI routes

Meetings UI stays on klai-portal.

**Exit gate**:
- `pytest klai-portal/backend/tests/test_vexa_webhook.py` passes
- Manual portal smoke test (preview env or staging) confirms start-bot → call reaches
  meeting-api placeholder (can stub with `httpbin`)

---

## Phase 5 (Priority High) — Scribe integration changes

Goal: `WhisperHttpProvider` points at the new transcription-service with
`tier=deferred` and 503 retry. Zero touch on scribe volume or DB.

### 5.1 Update `klai-scribe/scribe-api/app/services/providers.py`

```python
import asyncio
from typing import Final

_DEFERRED_TIER: Final = "deferred"
_MAX_RETRIES: Final = 3
_RETRY_AFTER_CLAMP: Final = (1, 30)  # seconds


class WhisperHttpProvider:
    """Calls vexa-transcription-service POST /v1/audio/transcriptions with tier=deferred."""

    async def transcribe(
        self,
        audio_wav: bytes,
        language: str | None,
    ) -> TranscriptionResult:
        data: dict = {"transcription_tier": _DEFERRED_TIER}
        if language:
            data["language"] = language

        attempt = 0
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    resp = await client.post(
                        f"{settings.whisper_server_url}/v1/audio/transcriptions",
                        files={"file": ("audio.wav", audio_wav, "audio/wav")},
                        data=data,
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                logger.exception("transcription-service unreachable",
                                 attempt=attempt, url=settings.whisper_server_url)
                if attempt >= _MAX_RETRIES:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Transcriptie tijdelijk niet beschikbaar",
                    ) from exc
                await asyncio.sleep(min(2 ** (attempt - 1), _RETRY_AFTER_CLAMP[1]))
                continue

            if resp.status_code == 503 and attempt < _MAX_RETRIES:
                retry_after = resp.headers.get("Retry-After", "1")
                try:
                    wait_s = max(_RETRY_AFTER_CLAMP[0],
                                 min(int(retry_after), _RETRY_AFTER_CLAMP[1]))
                except ValueError:
                    wait_s = 1
                logger.warning("transcription-service busy; retrying",
                               attempt=attempt, retry_after=wait_s)
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code != 200:
                logger.error("transcription-service error",
                             status=resp.status_code, body=resp.text[:200])
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Transcriptie tijdelijk niet beschikbaar",
                )

            payload = resp.json()
            return TranscriptionResult(
                text=payload["text"],
                language=payload["language"],
                duration_seconds=float(payload["duration"]),
                inference_time_seconds=float(payload["inference_time_seconds"]),
                provider=settings.whisper_provider_name,
                model=payload.get("model", "large-v3-turbo"),
            )
```

Notes:
- Uses `logger.exception()` per klai python rules
- Catches `ConnectError` separately from generic `Exception`
- Uses `asyncio.sleep` not time.sleep
- No change to `SpeechProvider` Protocol or `TranscriptionResult` dataclass — response
  schema is identical (OpenAI-compatible)

### 5.2 Update test fixtures

`klai-scribe/scribe-api/tests/test_providers.py` — add two test cases:
- 503 + `Retry-After: 2` on first attempt → 200 on second attempt → returns result
- 3× 503 → raises `HTTPException(503)`

### 5.3 Confirm no scribe data touched

```bash
# Before start of Phase 5
docker run --rm -v scribe-audio-data:/d alpine sh -c 'find /d -type f | wc -l; du -sb /d | cut -f1' \
  > reports/scribe-snapshot-pre.txt

# After Phase 5 code is deployed and before Phase 6
# (repeat; must match pre value exactly)
```

**Exit gate**:
- Scribe tests pass: `cd klai-scribe/scribe-api && uv run pytest tests/test_providers.py -v`
- `ruff check` + `pyright` pass
- `reports/scribe-snapshot-pre.txt` captured

---

## Phase 6 (Priority High) — Build, wipe, deploy

Goal: Images built on core-01, database wiped, stack up and healthy.

### 6.1 Build images on core-01

```bash
ssh core-01 bash <<'EOS'
set -euo pipefail
cd /tmp
rm -rf vexa-src
git clone https://github.com/mvletter/vexa.git vexa-src
cd vexa-src
BRANCH="klai/main-YYMMDD-<sha>"   # from Phase 1
git checkout "$BRANCH"
echo "Building from $(git rev-parse HEAD)..."
make build            # upstream's pipeline; writes BUILD_TAG to deploy/compose/.last-tag
BUILD_TAG=$(cat deploy/compose/.last-tag)
echo "Built images with tag: $BUILD_TAG"
# Also build vexa-bot
make build-bot-image
# Build transcription-service for gpu-01 (CUDA image, built on core-01 is fine)
docker build -t "vexaai/transcription-service:${BUILD_TAG}" \
  -f services/transcription-service/Dockerfile services/transcription-service/
# Export for gpu-01
docker save "vexaai/transcription-service:${BUILD_TAG}" | gzip > /tmp/transcription-service.tar.gz
echo "$BUILD_TAG" > /opt/klai/.vexa-build-tag
EOS
```

### 6.2 Pin tags in deploy files

Use the value from `/opt/klai/.vexa-build-tag` to rewrite every `YYMMDD-HHMM` placeholder
in `deploy/docker-compose.yml` and `deploy/docker-compose.gpu.yml`:

```bash
TAG=$(ssh core-01 'cat /opt/klai/.vexa-build-tag')
sed -i.bak "s|0\.10\.0-YYMMDD-HHMM|${TAG}|g" deploy/docker-compose.yml deploy/docker-compose.gpu.yml
./deploy/check-image-tags.sh   # must pass now
git add deploy/docker-compose.yml deploy/docker-compose.gpu.yml deploy/check-image-tags.sh \
        deploy/vexa/profiles.yaml deploy/VERSIONS.md
```

Update `deploy/VERSIONS.md` with a new row per Vexa service.

### 6.3 Backup old images

```bash
ssh core-01 bash <<'EOS'
set -e
mkdir -p /backup/vexa-rollback
docker save vexa-meeting-api:klai  | gzip > /backup/vexa-rollback/vexa-meeting-api-600cba04-$(date +%y%m%d).tar.gz
docker save vexa-runtime-api:klai  | gzip > /backup/vexa-rollback/vexa-runtime-api-600cba04-$(date +%y%m%d).tar.gz
ls -la /backup/vexa-rollback/
EOS
```

### 6.4 Wipe vexa database

```bash
ssh core-01 bash <<'EOS'
set -e
# Stop old containers first so no connections hold the DB
docker compose -f /opt/klai/docker-compose.yml stop vexa-meeting-api vexa-runtime-api || true
# Drop + recreate
docker exec klai-core-postgres-1 psql -U klai -c "DROP DATABASE IF EXISTS vexa;"
docker exec klai-core-postgres-1 psql -U klai -c "CREATE DATABASE vexa OWNER vexa;"
docker exec klai-core-postgres-1 psql -U klai -c "ALTER DATABASE vexa SET idle_in_transaction_session_timeout = 60000;"
EOS
```

### 6.5 Pre-create Garage bucket

```bash
ssh core-01 'docker exec klai-core-garage-1 /garage bucket create vexa-recordings || true'
ssh core-01 'docker exec klai-core-garage-1 /garage bucket allow --read --write --key <garage-key> vexa-recordings'
```

### 6.6 Commit, push, deploy

Follow `klai-portal/CLAUDE.md` deploy workflow:
```bash
git add -A
git commit -m "feat(vexa): rebuild on upstream main (SPEC-VEXA-003)"
git push
gh run watch --exit-status
```

If there is a manual deploy step for `klai-infra` (outside the GitHub Action),
document it here. Otherwise rely on the Action.

### 6.7 Start new stack

```bash
ssh core-01 bash <<'EOS'
set -e
cd /opt/klai
# Remove old containers + their volumes (vexa-redis keeps data; safe to recreate)
docker compose stop vexa-meeting-api vexa-runtime-api || true
docker compose rm -f vexa-meeting-api vexa-runtime-api || true
# Start new set
docker compose up -d admin-api api-gateway meeting-api runtime-api vexa-redis
# Wait for healthchecks
sleep 30
docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
EOS
```

### 6.8 Deploy transcription-service on gpu-01

```bash
scp /tmp/transcription-service.tar.gz core-01:/tmp/
ssh core-01 bash <<'EOS'
set -e
# Copy to gpu-01 via existing tunnel method
scp -i /opt/klai/gpu-tunnel-key /tmp/transcription-service.tar.gz root@5.9.10.215:/tmp/
ssh -i /opt/klai/gpu-tunnel-key root@5.9.10.215 <<'GPU_EOS'
  docker load < /tmp/transcription-service.tar.gz
  cd /opt/klai
  docker compose -f docker-compose.gpu.yml stop whisper-server || true
  docker compose -f docker-compose.gpu.yml rm -f whisper-server || true
  docker compose -f docker-compose.gpu.yml up -d transcription-worker-1 transcription-worker-2 transcription-api
  sleep 60  # model cold load
  curl -sf http://127.0.0.1:8000/health
GPU_EOS
EOS
```

### 6.9 Verify tunnel + consumers still reach :8000

```bash
ssh core-01 'docker exec klai-core-portal-api-1 curl -sf --max-time 5 http://172.18.0.1:8000/health'
ssh core-01 'docker exec klai-core-scribe-api-1 curl -sf --max-time 5 http://172.18.0.1:8000/health'
```

**Exit gate**:
- All acceptance §C, §D, §E checks pass
- No services in restart loops (`docker compose ps` shows healthy/Up)

---

## Phase 7 (Priority High) — End-to-end verification

Goal: Run every acceptance scenario on the live deploy. No "looks correct" claims —
evidence attached per test.

### 7.1 Acceptance C + D + E

Run the commands from acceptance.md §C, §D, §E. Capture outputs in `reports/phase7-health.txt`.

### 7.2 Acceptance F — real Google Meet flow via Playwright

Run the Playwright script (browser MCP) against `https://my.getklai.com`:
1. Login as `playwright-test@getklai.com`
2. Create meeting at `https://meet.google.com/test-vexa003-run1`
3. Assert within 60s: bot container running, status=recording
4. Speak via browser voice (or dummy participant) for 5+ seconds
5. Assert transcript text appears within 60s of speech
6. End meeting; assert exactly 1 `meeting.completed` event

Repeat three times (F.4 dedup verification).

### 7.3 Acceptance G — webhook auth

Run both curl commands from acceptance.md §G. Capture response codes.

### 7.4 Acceptance H — tier=deferred + 503 retry

Run load test to saturate realtime slots; trigger scribe upload; verify 503 retry path.

### 7.5 Acceptance I — fork fix verification

Run a 3-person Google Meet (use two additional test accounts or a confederate). Verify
`speaker_events` distinctness and absence of WebRTC errors in bot logs.

### 7.6 Acceptance L — trace correlation

Grab a `request_id` from portal-api logs (`service:portal-api` in VictoriaLogs). Query
`request_id:<uuid>` — expect logs from portal-api + meeting-api + runtime-api + vexa-bot.

### 7.7 Acceptance N — Redis 8 forward-compatibility (NEW)

Run every sub-criterion from acceptance.md §N on the live deploy:

- **§N.1** — container starts + PING under `requirepass`; verify `redis_version:8.*`
- **§N.2** — meeting-api ↔ runtime-api pub/sub round-trip (publisher reports `subs=1`;
  subscriber receives the message)
- **§N.3** — streams + consumer-group operations (`XADD`, `XGROUP CREATE`,
  `XREADGROUP`, `XACK`, `XPENDING`) execute without error; AND full real-meeting
  §F flow produces transcript segments end-to-end
- **§N.4** — webhook retry queue: force a delivery failure, observe queue grows, restore
  URL, observe queue drains to 0
- **§N.5** — `docker logs klai-core-vexa-redis-1 --since 10m 2>&1 | grep -iE "deprecated|warning"`
  returns zero matches

**If any sub-criterion fails**, trigger §N.6 rollback:

```bash
# Single-line compose change
sed -i 's|image: redis:8-alpine|image: redis:7.0-alpine|' deploy/docker-compose.yml
git commit -am "fix(vexa-redis): rollback to 7.0 — REQ-U-006 tests failed"
git push && gh run watch --exit-status
# On core-01
docker compose up -d vexa-redis
# Re-run §N.1–§N.5 on 7.0; update research.md §5.3 conclusion with evidence
```

No change to meeting-api, runtime-api, or any other service is required for the rollback.
`vexa-redis-data` volume is new on first boot of this migration; consumer-group cursors
live in it, so optionally `docker run --rm -v vexa-redis-data:/src -v /backup:/dst alpine
tar czf /dst/vexa-redis-predowngrade.tgz -C /src .` before the image flip.

**Exit gate**: All acceptance items pass, evidence attached. Any failure → halt, fix,
re-verify.

---

## Phase 8 (Priority Medium) — Cleanup and SPEC sync

### 8.1 Remove old images

```bash
ssh core-01 'docker image prune -f'
ssh core-01 'docker rmi vexa-meeting-api:klai vexa-runtime-api:klai || true'
ssh -i /opt/klai/gpu-tunnel-key root@5.9.10.215 'docker rmi ghcr.io/getklai/whisper-server:latest || true'
```

### 8.2 Mark predecessor SPECs

Edit `.moai/specs/SPEC-VEXA-001/spec.md` frontmatter:
```yaml
status: completed-superseded-by-VEXA-003
```

Edit `.moai/specs/SPEC-VEXA-002/spec.md` frontmatter:
```yaml
status: cancelled-folded-into-VEXA-003
```

### 8.3 Update architecture docs

`docs/architecture/platform.md` — update the Vexa section to describe the new topology
(admin-api, api-gateway, meeting-api, runtime-api, transcription-service on gpu-01).

### 8.4 Update `deploy/VERSIONS.md`

Final form includes every new vexa image with tag, git SHA, build date.

### 8.5 Remove `klai-scribe/whisper-server/` (if present)

Only if fully replaced; verify no consumer still references it.

```bash
cd klai-scribe
grep -r "whisper-server" --include="*.py" --include="*.yml" --include="*.yaml" --include="*.md" .
# only expected matches: historical docs and the archived SPEC-VEXA-002; no active code
rm -rf whisper-server/
```

**Exit gate**:
- Commit + push + `gh run watch --exit-status` → green
- All documentation reflects new state

---

## Risk register

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | `:latest` drift repeats VEXA-001 Fault 1 | Critical | Pre-commit `check-image-tags.sh`; REQ-E-008; explicit SPEC exclusion; Phase 6.2 enforces before commit |
| R2 | Agent redesigns architecture during migration | Critical | This SPEC IS the architecture; §4 Exclusions lists what is NOT to be changed; `no-architecture-change-in-migration` pitfall referenced |
| R3 | Scribe data loss | Critical | Pre/post row count + volume byte count in acceptance §B; REQ-N-001 fail-fast |
| R4 | Fork fix rebase fails / skipped incorrectly | High | Phase 1.2 decision matrix; acceptance §I verifies functionally; if both fixes skipped, 3-person test still verifies behaviour |
| R5 | Webhook payload shape regression | Medium | Phase 4.2 unit test with upstream envelope fixture; existing `VexaWebhookPayload._normalize` handles both |
| R6 | gpu-01 VRAM insufficient | Medium | Same model, same compute type as whisper-server (3GB); `nvidia-smi` monitored during Phase 6.8; fall back to single worker by commenting worker-2 |
| R7 | SSH tunnel `gpu-tunnel.service` breaks on port change | Medium | Retain host port 8000 via `127.0.0.1:8000:80` mapping; gpu-tunnel unchanged |
| R8 | Playwright test flaky due to Google Meet UI changes | Medium | Use dedicated `playwright-test@getklai.com` account; record HAR on failure; fall back to manual verification with two humans |
| R9 | Caddy + dashboard routing not designed up front | Low | Dashboard is NOT shipped in v1; no Caddy change needed. Documented deviation in spec.md §4 |
| R10 | Garage S3 compatibility gap vs MinIO | Low | `RECORDING_ENABLED=false` by default; uploads/downloads exercised only in load test; fall-back: deploy dedicated MinIO if Garage rejects a multipart path |
| R11 | Network alias `vexa-meeting-api` collides | Low | Alias applied only on `klai-net`; tested by docker compose config before up |
| R12 | pg17 → pg18 major version skew | Low | Vexa schema uses standard SQL + asyncpg; no pg18-incompatible features known; backed up via logical dump if needed |
| R13 | Redis 7 → 8 forward-compat issue (upstream pins 7) | Low-Med | Phase 7.7 executes §N.1–N.5 tests; single-line rollback documented; no data migration needed because vexa-redis-data is new on first boot |
| R14 | HMAC-SHA256 adoption demanded post-v1 | Low | Documented in REQ-O-004 as future work; per-client webhook path exists upstream |
| R15 | Worktree-based build leaks files outside main working tree | Low | `spec-discipline` pitfall enforced; Phase 6 builds on core-01 directly, not in a Claude Code worktree |

---

## Rollback strategy

Klai is in test mode with no paying customers (user-confirmed). Rollback is tiered:

### Tier 1 — roll forward (primary)

For any in-phase failure, fix in place. Accept minutes to hours of downtime in the Vexa
stack. Scribe continues working independently once Phase 5 code is deployed (scribe
consumer is decoupled from meeting bot).

### Tier 2 — data rescue (always done)

Before ANY migration step:

```bash
# Phase 6.3 captures these, verified in acceptance §M.1
docker save vexa-meeting-api:klai | gzip > /backup/vexa-rollback/vexa-meeting-api-600cba04-YYMMDD.tar.gz
docker save vexa-runtime-api:klai | gzip > /backup/vexa-rollback/vexa-runtime-api-600cba04-YYMMDD.tar.gz
```

Retain for 30 days. Immutable (`chmod 444`).

### Tier 3 — full rollback

If the new stack cannot be stabilised and user decides to revert:

```bash
# 1. Stop and remove new containers
docker compose stop admin-api api-gateway meeting-api runtime-api
docker compose rm -f admin-api api-gateway meeting-api runtime-api

# 2. Restore old images
docker load < /backup/vexa-rollback/vexa-meeting-api-600cba04-YYMMDD.tar.gz
docker load < /backup/vexa-rollback/vexa-runtime-api-600cba04-YYMMDD.tar.gz

# 3. Revert deploy/docker-compose.yml
git checkout HEAD~N -- deploy/docker-compose.yml deploy/vexa/profiles.yaml deploy/docker-compose.gpu.yml

# 4. Restore whisper-server on gpu-01
ssh -i /opt/klai/gpu-tunnel-key root@5.9.10.215 'docker compose -f /opt/klai/docker-compose.gpu.yml up -d whisper-server'

# 5. Start old vexa stack
docker compose up -d vexa-meeting-api vexa-runtime-api vexa-redis

# 6. Accept: vexa DB was wiped — bot transcripts from the migration window are lost
#    (pre-existing decision, documented)
```

### Tier 3a — Redis-only rollback (NEW, per REQ-U-006)

Isolated rollback path if only the Redis 8 compatibility test fails:

```bash
# Single-line change in deploy/docker-compose.yml
sed -i 's|image: redis:8-alpine|image: redis:7.0-alpine|' deploy/docker-compose.yml
git commit -am "fix(vexa-redis): downgrade to 7.0 after forward-compat test failure"
git push && gh run watch --exit-status

# On core-01
docker compose up -d vexa-redis   # volume preserved; consumer groups intact
```

No other service or code changes are required. Document the failing sub-criterion
from §N in research.md §5.3 HISTORY.

### Tier 4 — data rescue only (guaranteed)

Scribe data is guaranteed untouched via:

- Explicit REQ-U-003 + REQ-N-001 + Exclusions list in spec.md §4
- Volume file-count and byte-count snapshots pre/post (acceptance §B.1)
- Row count snapshot pre/post for all `scribe_*` postgres tables (acceptance §B.2)
- Grep check that no vexa service block mentions `scribe-audio-data` (acceptance §B.3)

If any Tier 4 check fails during Phase 6 or 7, halt immediately and do NOT proceed to
cleanup in Phase 8.

---

End of plan.md.
