# SPEC-VEXA-003: Research — Clean-Slate Rebuild on Upstream Vexa main (v0.10)

> Deep codebase + upstream analysis that backs spec.md / plan.md / acceptance.md.
> Sources: `upstream/main` of Vexa-ai/vexa (verified via `git show upstream/main:...`),
> Klai deploy manifests, klai-portal backend, klai-scribe API, prior SPEC-VEXA-001/002.

---

## 1. Executive summary

Klai currently runs a Vexa meeting bot stack built from the abandoned `feature/agentic-runtime`
branch at commit `600cba04` (SPEC-VEXA-001 delivered). That branch has been superseded upstream
by a modular release on `main` (0.10 track, last commit `f0756bf`, 1 ahead / 716 behind the
klai fork's active branch). SPEC-VEXA-002 (transcription-service swap) was drafted but never
implemented.

Rather than incrementally rebasing, this SPEC defines a **clean-slate rebuild** on upstream main:

- Full replacement of `vexa-meeting-api:klai` + `vexa-runtime-api:klai` with the upstream
  service topology: `admin-api`, `api-gateway`, `meeting-api`, `runtime-api`, `mcp`,
  `tts-service`, `dashboard` (optional), plus supporting `redis`, `postgres`, `minio`.
- Replacement of `klai-scribe/whisper-server/` on gpu-01 with upstream
  `services/transcription-service/` (CUDA build, Nginx LB, faster-whisper workers).
- Rebase of two local vexa-bot fixes (`fdb751f`, `787e517`) onto upstream main.
- Adoption of upstream's timestamped image tag convention (`VERSION-YYMMDD-HHMM`), pinned
  explicitly in `deploy/docker-compose.yml` to avoid the `:latest` pitfall from VEXA-001.

Klai is in test mode only (no paying customers). User has explicitly authorised data loss for
the `vexa` postgres database. Scribe test data (`scribe-audio-data` volume + scribe tables)
must be preserved unchanged.

---

## 2. Current Klai deploy state (core-01)

Source: `deploy/docker-compose.yml` lines 693-808.

### 2.1 Vexa services currently running

| Service | Image | Networks | Memory | Notes |
|---|---|---|---|---|
| `vexa-meeting-api` | `vexa-meeting-api:klai` (local build from `feature/agentic-runtime` @ `600cba04`) | klai-net, net-postgres, vexa-bots | 512M | Ports NOT exposed; portal-api talks to it on klai-net |
| `vexa-runtime-api` | `vexa-runtime-api:klai` (same source) | vexa-bots | 256M | Mounts `/var/run/docker.sock`; `group_add: 988` |
| `vexa-redis` | `redis:8-alpine` | vexa-bots | 128M | Password-protected via `VEXA_REDIS_PASSWORD` |
| `vexa-bot` (ephemeral) | `vexaai/vexa-bot:v20260315-2220` (Docker Hub public) | vexa-bots | 1536Mi | Spawned by runtime-api via Docker socket |

### 2.2 Current env-vars set on vexa-meeting-api

```
DB_HOST=postgres, DB_PORT=5432, DB_NAME=vexa, DB_USER=vexa, DB_PASSWORD=${VEXA_DB_PASSWORD}
REDIS_URL=redis://:${VEXA_REDIS_PASSWORD}@vexa-redis:6379/0
API_KEYS=${VEXA_API_KEY}
ADMIN_TOKEN=${VEXA_ADMIN_TOKEN}
RUNTIME_API_URL=http://vexa-runtime-api:8090
MEETING_API_URL=http://vexa-meeting-api:8080
BOT_IMAGE_NAME=vexaai/vexa-bot:v20260315-2220
TRANSCRIPTION_SERVICE_URL=http://172.18.0.1:8000/v1/audio/transcriptions
TRANSCRIPTION_SERVICE_TOKEN=internal
STORAGE_BACKEND=local, RECORDING_ENABLED=false
POST_MEETING_HOOKS=http://portal-api:8010/api/bots/internal/webhook
TRANSCRIPTION_COLLECTOR_URL=http://vexa-meeting-api:8080
```

### 2.3 Vexa services currently NOT deployed by Klai

- admin-api (Zitadel handles users)
- api-gateway (portal-api speaks to meeting-api directly on klai-net)
- mcp (not consumed by klai-agent-stack today)
- dashboard (klai-portal replaces it)
- tts-service (out of scope)
- minio (`STORAGE_BACKEND=local`, `RECORDING_ENABLED=false`)
- telegram-bot, calendar-service, agent-api (no-ship for 0.10 upstream, not needed)

### 2.4 Profiles.yaml (current)

`deploy/vexa/profiles.yaml`:

```yaml
profiles:
  meeting:
    image: "vexaai/vexa-bot:v20260315-2220"
    resources:
      memory_limit: "1536Mi"
      memory_request: "512Mi"
      cpu_limit: "2000m"
      cpu_request: "500m"
      shm_size: 1073741824  # 1GB
    idle_timeout: 7200
    auto_remove: true
    env:
      LOG_LEVEL: "INFO"
    network_mode: "vexa-bots"
```

### 2.5 Scribe (not to be touched on volume/db level)

`deploy/docker-compose.yml` line 693-708:

```yaml
scribe-api:
  image: ghcr.io/getklai/scribe-api:latest
  environment:
    POSTGRES_DSN: postgresql+asyncpg://klai:${POSTGRES_PASSWORD}@postgres:5432/klai
    WHISPER_SERVER_URL: http://172.18.0.1:8000
    ZITADEL_ISSUER: https://auth.${DOMAIN}
  volumes:
    - scribe-audio-data:/data/audio
```

Scribe provider — `klai-scribe/scribe-api/app/services/providers.py`:

- Single `WhisperHttpProvider` class, posts to `${WHISPER_SERVER_URL}/v1/audio/transcriptions`
- `httpx.AsyncClient(timeout=300.0)`, multipart `files={"file":("audio.wav", ...)}`
- Returns `TranscriptionResult(text, language, duration_seconds, inference_time_seconds, provider, model)`
- Raises `HTTPException(503)` on connect error or non-200 — no internal retry logic

Scribe database tables (in `klai` postgres database, `scribe_` prefix or dedicated schema —
verify during Phase 2 research before migration touches postgres).

### 2.6 Klai-portal Vexa integration — current contracts

**Config** (`klai-portal/backend/app/core/config.py` lines 132-136):

```python
vexa_meeting_api_url: str = "http://vexa-meeting-api:8080"
vexa_admin_token: str = ""
vexa_api_key: str = ""
vexa_webhook_secret: str = ""
```

**Client** (`klai-portal/backend/app/services/vexa.py`):

- `VexaClient` — single `httpx.AsyncClient`, `X-API-Key: {vexa_api_key}` header, 60s timeout
- Methods: `start_bot`, `stop_bot`, `get_running_bots` (GET `/bots/status`),
  `get_recording`, `get_transcript_segments`, `delete_recording`
- URL parser supports google_meet, zoom, teams (zoom returns numeric id; teams uses sha256 hash)
- `start_bot` payload:
  ```json
  {"platform": "...", "native_meeting_id": "...", "recording_enabled": false,
   "bot_name": "Klai",
   "automatic_leave": {"max_time_left_alone": 30000, "no_one_joined_timeout": 120000,
                       "max_wait_for_admission": 120000}}
  ```

**Webhook handler** (`klai-portal/backend/app/api/meetings.py`):

- Route: `POST /api/bots/internal/webhook`
- Auth (`_require_webhook_secret`, line 45):
  - Trust `172.x`, `10.x`, `192.168.x` source IPs implicitly
  - If `vexa_webhook_secret` is set AND caller is not internal, require
    `Authorization: Bearer {vexa_webhook_secret}` header
  - Currently: no HMAC-SHA256 signature verification
- Payload model `VexaWebhookPayload` (line 428) accepts BOTH envelope and flat shapes:
  ```python
  @model_validator(mode="before")
  def _normalize(cls, data):
      if "meeting" in data and "platform" not in data:
          # envelope: {"event_type", "meeting": {"id","platform","native_meeting_id","status","end_time"}, "recording":{"id"}}
          return {"vexa_meeting_id": meeting["id"], "platform": ..., ...}
      return {**data, "vexa_meeting_id": data.get("id")}
  ```
- Status mapping (line 618):
  ```
  "joining" / "awaiting_admission" → "joining"
  "active" / "recording" → "recording"
  "failed" → "failed"
  "completed" → triggers run_transcription + cleanup + emit meeting.completed event
  ```
- Dedup: Uses `SELECT ... WHERE status IN (ACTIVE_STATUSES, "stopping") ORDER BY created_at DESC`.
  Only the most-recent active row is selected; completed rows cannot be transitioned again.
  Upstream April 18 dedup fix (status path no longer double-fires `meeting.completed`) does
  NOT require changes here — klai's handler is already idempotent on the portal side.

### 2.7 Current transcription path on gpu-01

`deploy/docker-compose.gpu.yml`:

```yaml
whisper-server:
  image: ghcr.io/getklai/whisper-server:latest
  # 146-line custom FastAPI, single asyncio.Lock, no tier, no VAD, no backpressure
```

Tunnel: `gpu-tunnel.service` (systemd, core-01) exposes `gpu-01:8000` at `172.18.0.1:8000`
on core-01 Docker networks. Key at `/opt/klai/gpu-tunnel-key`. Per
`.claude/rules/klai/infra/servers.md`, direct access to gpu-01 is via core-01 only.

---

## 3. Upstream main (v0.10 track) architecture

Source: `upstream/main` of Vexa-ai/vexa, commit `f0756bf` and neighbours. Verified via
`git show upstream/main:...`.

### 3.1 Deploy layout

- `deploy/compose/docker-compose.yml` — canonical production stack
- `deploy/compose/Makefile` — build + publish pipeline with image tagging
- `deploy/lite/` — single-container lightweight variant (not used by Klai)
- `deploy/helm/` — Kubernetes chart (not used by Klai)
- `deploy/env-example` — reference env file

### 3.2 Services in `deploy/compose/docker-compose.yml`

#### Infrastructure
| Service | Image | Port | Notes |
|---|---|---|---|
| `redis` | `redis:7.0-alpine` | internal | `--appendonly yes --appendfsync everysec`, 512m |
| `postgres` | `postgres:17-alpine` | `5458:5432` | `idle_in_transaction_session_timeout=60000` |
| `minio` | `minio/minio:latest` | `9000:9000`, `9001:9001` (console) | 1g limit |
| `minio-init` | `minio/mc:latest` | — | One-shot; creates bucket `${MINIO_BUCKET}` |

#### Core services
| Service | Build context | Image | Port | Key env |
|---|---|---|---|---|
| `admin-api` | `services/admin-api/Dockerfile` | `vexaai/admin-api:${IMAGE_TAG}` | `8057:8001` | `ADMIN_API_TOKEN`, `INTERNAL_API_SECRET`, DB_* |
| `runtime-api` | `services/runtime-api/Dockerfile` | `vexaai/runtime-api:${IMAGE_TAG}` | `8090:8090` | `DOCKER_NETWORK`, `AGENT_IMAGE`, `BROWSER_IMAGE`, `REDIS_URL`, `MINIO_*`, `BOT_API_TOKEN`, DB_*, `PROFILES_PATH=/app/profiles.yaml`, `ALLOW_PRIVATE_CALLBACKS=1`; mounts `/var/run/docker.sock` and `profiles.yaml`; `group_add: ${DOCKER_GID:-998}` |
| `api-gateway` | `services/api-gateway/Dockerfile` | `vexaai/api-gateway:${IMAGE_TAG}` | `8056:8000` | `ADMIN_API_URL`, `MEETING_API_URL`, `TRANSCRIPTION_COLLECTOR_URL=http://meeting-api:8080`, `MCP_URL`, `REDIS_URL`, `INTERNAL_API_SECRET`, `RATE_LIMIT_RPM`, `CORS_ORIGINS`; 1g |
| `meeting-api` | `services/meeting-api/Dockerfile` | `vexaai/meeting-api:${IMAGE_TAG}` | `expose 8080` (internal only) | DB_*, `REDIS_URL`, `ADMIN_TOKEN`, `RUNTIME_API_URL`, `RUNTIME_API_TOKEN`, `MEETING_API_URL`, `BOT_IMAGE_NAME=${BROWSER_IMAGE}`, `DOCKER_NETWORK`, `TTS_SERVICE_URL`, `POST_MEETING_HOOKS`, `TRANSCRIPTION_COLLECTOR_URL=http://meeting-api:8080` (self-reference — collector integrated), `STORAGE_BACKEND=${STORAGE_BACKEND:-minio}`, `MINIO_*`, `RECORDING_ENABLED=${...:-true}`, `CAPTURE_MODES=${...:-audio}`, `TRANSCRIPTION_SERVICE_URL`, `TRANSCRIPTION_SERVICE_TOKEN`, `TRANSCRIPTION_GATEWAY_URL`, `OPENAI_API_KEY` (cloud fallback); 1g; volume `${LOCAL_STORAGE_VOLUME_SOURCE:-recordings-data}:/data/recordings` |
| `mcp` | `services/mcp/Dockerfile` | `vexaai/mcp:${IMAGE_TAG}` | `18888:18888` | `API_GATEWAY_URL=http://api-gateway:8000`; 512m |
| `dashboard` | `services/dashboard/Dockerfile` | `vexaai/dashboard:${IMAGE_TAG}` | `3001:3000` | `VEXA_API_URL=http://api-gateway:8000`, `VEXA_API_KEY`, `VEXA_ADMIN_API_KEY`, `VEXA_ADMIN_API_URL`, `JWT_SECRET`; Next.js, 1g |
| `tts-service` | `services/tts-service/Dockerfile` | `vexaai/tts-service:${IMAGE_TAG}` | `expose 8002` | `TTS_API_TOKEN`; volume `tts-voices:/app/voices`; 1g |

#### NO-SHIP for v0.10 (commented out in main compose)
- `agent-api` — Claude Code CLI runner, not used by Klai
- `telegram-bot` — commented out
- `calendar-service` — commented out; Klai has its own IMAP listener

### 3.3 Runtime-api profiles.yaml (upstream)

Source: `services/runtime-api/profiles.yaml`.

```yaml
profiles:
  meeting:
    image: "${BROWSER_IMAGE}"           # not hardcoded — comes from .env
    command: ["/app/vexa-bot/entrypoint.sh"]
    working_dir: "/app/vexa-bot"
    resources:
      cpu_request: "1000m"   # measured p95: 780m (load test 2026-04-15, 19 bots)
      cpu_limit: "1500m"     # 1.5x headroom
      memory_request: "1100Mi"   # measured p95: 977Mi
      memory_limit: "1536Mi"
      shm_size: 2147483648   # 2GB — upstream raised from 1GB
    idle_timeout: 0          # managed by meeting-api scheduler
    auto_remove: false       # upstream flipped from true
    env:
      DISPLAY: "${DISPLAY:-:99}"
      NODE_ENV: "production"
      REDIS_URL: "${REDIS_URL}"
      TRANSCRIPTION_SERVICE_URL: "${TRANSCRIPTION_SERVICE_URL}"
      TRANSCRIPTION_SERVICE_TOKEN: "${TRANSCRIPTION_SERVICE_TOKEN}"
    ports:
      "9222/tcp": {}

  browser-session: ...          # persistent Chrome for VNC/CDP (not needed by Klai)
  agent: ...                    # claude-cli runner (not needed by Klai)
```

**Schema deltas vs current klai profiles.yaml:**

| Field | Klai current | Upstream main |
|---|---|---|
| `image` | hardcoded `vexaai/vexa-bot:v20260315-2220` | `"${BROWSER_IMAGE}"` — resolved from env |
| `shm_size` | 1 GiB (1073741824) | 2 GiB (2147483648) |
| `idle_timeout` | 7200 (2h, runtime-api kills) | 0 (meeting-api scheduler owns it) |
| `auto_remove` | true | false |
| `command` / `working_dir` | absent | explicit entrypoint |
| `env` | `LOG_LEVEL: INFO` only | `DISPLAY`, `NODE_ENV`, `REDIS_URL`, `TRANSCRIPTION_SERVICE_*` |
| `network_mode` | "vexa-bots" (string) | absent — runtime-api uses `DOCKER_NETWORK` env |

### 3.4 Image tagging convention (upstream)

From `deploy/compose/Makefile`:

```make
VERSION := $(shell cat $(ROOT)/VERSION 2>/dev/null || echo "0.0.0")
BUILD_TAG := $(VERSION)-$(shell date +%y%m%d-%H%M)
```

- `make build` — writes fresh `BUILD_TAG` into `deploy/compose/.last-tag`
- `make up` — reads `IMAGE_TAG` from `.env` (default `dev`) OR from `.last-tag` if present
- `make publish` — pushes all images + sets `:dev` mutable pointer
- `make promote-staging TAG=…`, `make promote-latest TAG=…` — re-tag mutable pointers

Example built tags: `0.10.0-260419-1530`, `0.10.0-260418-0921`. Immutable timestamp +
version. Mutable pointers (`:dev`, `:staging`, `:latest`) exist but should NOT be used
in `deploy/docker-compose.yml` (pitfall from SPEC-VEXA-001).

### 3.5 Webhook contract (upstream, post April 18 fixes)

Source: `services/meeting-api/meeting_api/webhook_delivery.py` and `webhooks.py`.

#### Envelope format (`WEBHOOK_API_VERSION = "2026-03-01"`)

```json
{
  "event_id": "evt_<uuid4hex>",
  "event_type": "meeting.completed",
  "api_version": "2026-03-01",
  "created_at": "2026-04-19T10:00:00+00:00",
  "data": {
    "meeting": {
      "id": 123,
      "user_id": 7,
      "user_email": "alice@example.com",
      "platform": "google_meet",
      "status": "completed",
      "duration_seconds": 482.5,
      "start_time": "2026-04-19T09:50:00+00:00",
      "end_time": "2026-04-19T09:58:02+00:00",
      "created_at": "2026-04-19T09:45:00+00:00",
      "transcription_enabled": true,
      "native_meeting_id": "abc-defg-hij"   // from meeting.data after clean_meeting_data
    }
  }
}
```

Internal fields stripped by `clean_meeting_data()`:
`webhook_delivery`, `webhook_deliveries`, `webhook_secret`, `webhook_secrets`,
`webhook_events`, `webhook_url`, `bot_container_id`, `container_name`.

#### Event types (`STATUS_TO_EVENT` in `webhooks.py`)

```python
STATUS_TO_EVENT = {"active": "meeting.started", "failed": "bot.failed"}
# meeting.completed fires ONLY from send_completion_webhook (post-meeting hook)
# — status path no longer double-fires (April 18 dedup fix)
```

Dedup behaviour: The status path explicitly excludes `"completed"` from `STATUS_TO_EVENT`
because `send_completion_webhook()` in `post_meeting.py` owns the `meeting.completed` payload.
Commit `19cff9d`: "fix(webhooks): status path no longer double-fires meeting.completed +
stop_requested gate no longer silences status webhooks".

#### HMAC-SHA256 signing

```python
def sign_payload(payload_bytes: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode(), payload_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"
```

Signing only applies to **per-client webhooks** (user-configured via
`PUT /user/webhook`, secret stored in `meeting.data.webhook_secret`). The
**internal hooks** (`POST_MEETING_HOOKS` env var, used by Klai) receive the same
envelope payload but **without HMAC signing** unless we configure a secret.

Implication for Klai: Portal-api currently authenticates via source-IP allowlist
(`172.x/10.x/192.168.x`) + optional bearer token. Upstream does NOT add a signature
on internal POST_MEETING_HOOKS automatically. To gain HMAC verification, we can either:
(a) keep the source-IP allowlist (sufficient — internal docker network is not reachable
from outside), or
(b) adopt the per-client webhook path: register portal-api via admin-api + `PUT /user/webhook`
with a shared secret; then upstream will HMAC-sign every delivery.

The SPEC specifies option (a) for Klai internal traffic (no reason to sign over a
Docker-internal bridge network). The HMAC path is documented as optional future work.

### 3.6 Reliability features (new in upstream main)

- **Redis-backed retry queue** for failed webhook deliveries (`webhook_retry_worker.py`)
  — durable retries across meeting-api restarts
- **`set_redis_client()`** called at app startup; `deliver()` then automatically enqueues
  failed deliveries to `RETRY_QUEUE_KEY = "webhook:retry_queue"`
- **Exponential backoff** via `@with_retry` decorator (`retry.py`)
- **`idle_in_transaction_session_timeout=60000`** on postgres — defence-in-depth against
  leaked DB sessions
- **Bounded concurrency** in transcription-service with tier-aware admission

### 3.7 Transcription-service (upstream `services/transcription-service/`)

Source: `services/transcription-service/main.py`, `docker-compose.yml`, `nginx.conf`,
`Dockerfile`.

**Deployment topology (upstream own compose):**

```
transcription-api (nginx:alpine, port 80, exposed 8083)
  ├── transcription-worker-1 (CUDA 12.3.2, faster-whisper, MODEL_SIZE, DEVICE=cuda)
  └── transcription-worker-2
      [+ optional worker-3 commented out in compose]
```

**Base image**: `nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04`. Dockerfile installs
Python 3.10 + `faster-whisper` (CTranslate2-based, no PyTorch). Separate `Dockerfile.cpu`
exists.

**tier=realtime/deferred IS in upstream/main** — verified. Key logic (main.py):

```python
MAX_CONCURRENT_TRANSCRIPTIONS = _env_int("MAX_ACTIVE_REQUESTS",
                                          _env_int("MAX_CONCURRENT_TRANSCRIPTIONS", 20))
REALTIME_RESERVED_SLOTS = _env_int("REALTIME_RESERVED_SLOTS", 1)
FAIL_FAST_WHEN_BUSY = _env_bool("FAIL_FAST_WHEN_BUSY", True)
BUSY_RETRY_AFTER_S = _env_int("BUSY_RETRY_AFTER_S", 1)

def _normalize_transcription_tier(raw):
    tier = (raw or "realtime").strip().lower()
    return tier if tier in ("realtime", "deferred") else "realtime"

def _deferred_capacity_available(active_rt, active_df):
    deferred_limit = max(0, MAX_CONCURRENT_TRANSCRIPTIONS - REALTIME_RESERVED_SLOTS)
    total_active = active_rt + active_df
    return deferred_limit > 0 and active_df < deferred_limit \
           and total_active < MAX_CONCURRENT_TRANSCRIPTIONS
```

Tier is read from **form field `transcription_tier`** OR **header `X-Transcription-Tier`**.
On `deferred` when no capacity: `HTTPException(503, headers={"Retry-After": BUSY_RETRY_AFTER_S})`.

**API contract (OpenAI-compatible):**

- `POST /v1/audio/transcriptions` — multipart form: `file`, optional `language`, optional
  `transcription_tier` (OR `X-Transcription-Tier` header); returns
  `{"text","language","duration","inference_time_seconds","segments":[...]}`

**Hallucination + VAD defaults:**

```
BEAM_SIZE=5, BEST_OF=5
COMPRESSION_RATIO_THRESHOLD=1.8
LOG_PROB_THRESHOLD=-1.0
NO_SPEECH_THRESHOLD=0.6
CONDITION_ON_PREVIOUS_TEXT=false
VAD_FILTER=true, VAD_FILTER_THRESHOLD=0.5
VAD_MIN_SILENCE_DURATION_MS=160, VAD_MAX_SPEECH_DURATION_S=15.0
USE_TEMPERATURE_FALLBACK=false  # opt-in chain [0.0..1.0]
```

### 3.8 Webhook-related upstream commits (last ~40 commits on main)

| Commit | Message |
|---|---|
| `19cff9d` | fix(webhooks): status path no longer double-fires meeting.completed + stop_requested gate no longer silences status webhooks |
| `d6ab3b6` | feat(webhooks): tighten e2e_status — assert non-meeting.completed fires |
| `dd0d979` | fix(dashboard): /webhooks page shows all event types |
| `0ac59fe` | fix: status webhooks fire for stop_bot fast-paths + delayed_stop_finalizer |
| `b953d44` | fix: enable status webhook delivery + add tracking |
| `fd2526c` | fix: webhook delivery + DB pool exhaustion + test hardening |
| `ea6cf34` | fix(helm): runtime-api profiles ConfigMap (meeting + browser-session) |
| `8fa68ee` | fix(tests): update test_resolve_event_type for new STATUS_TO_EVENT semantics |

The April 18 dedup incident (triage log referenced at `releases/260418-webhooks/`) is the
single biggest webhook fix. Klai's current webhook handler is already idempotent (selects
only active meetings), so the fix reduces duplicate events on Vexa's side — klai sees
fewer noise events, not fewer legitimate ones.

---

## 4. Local fork state & bugfix rebase

### 4.1 Local branches

```
c:/Users/markv/stack/02 - Voys/Code/vexa
remotes:
  origin    → mvletter/vexa (fork)
  upstream  → Vexa-ai/vexa
branches:
  fix/participant-registry-mutation-observer  (fdb751f)  ← active
  fix/video-block-webrtc-stability            (787e517)
  main
```

### 4.2 Divergence & commit-600cba04 lookup

- Active fork branch is 1 ahead / 716 behind `upstream/main`
- Commit `600cba04` (the SPEC-VEXA-001 production pin) is NOT reachable from
  the fork's current branches — squashed or dropped during upstream's 716-commit
  post-agentic-runtime rewrite
- Our local fixes were authored against the pre-rewrite codebase

### 4.3 Fix commits to rebase

**`fdb751f` — fix: replace text-scan participant counting with MutationObserver registry**
- File scope: `services/vexa-bot/` only
- Context: Patches the Google Meet participant-counting logic (post-#190 fork patch);
  replaces a DOM text scan with a MutationObserver registry for stability
- Risk: Medium. Upstream bot code was refactored heavily between `600cba04` and current main.
  Participant counting likely touches `platforms/googlemeet/recording.js` or a successor
  file. If the target file is renamed or the logic is already fixed upstream, the rebase
  becomes a manual port (extract diff, reapply semantically)
- Validation: Google Meet with 3 real participants — bot must emit correct speaker_events

**`787e517` — fix: remove track.stop() and transceiver renegotiation from video block**
- File scope: `services/vexa-bot/` only
- Context: WebRTC stability; removes an overzealous `track.stop()` + transceiver
  renegotiation that caused Chrome WebRTC failures when a bot tried to block incoming
  video
- Risk: Medium-low. Video-block logic is small and upstream mentioned a "disable-incoming-video"
  feature branch that may have merged related work. Verify that the upstream behaviour is
  already correct before reapplying; if yes, skip the cherry-pick

**Rebase plan:**

1. Branch `klai/main-YYMMDD` off `upstream/main`
2. Verify each fix against upstream: does the bug already exist in upstream main? If yes,
   cherry-pick; if no, skip and log why
3. If cherry-pick conflicts, port the semantic change manually, commit with
   `fix(vexa-bot): ... (ported from fdb751f onto upstream main)`
4. Tag `klai/main-YYMMDD-<short-sha>` as the source commit for the build

---

## 5. Decision matrices

### 5.1 Storage: Garage (reuse) vs new MinIO

Klai already runs Garage (`dxflrs/garage:v2.3.0`) on `klai-net` for knowledge-pipeline
images. Garage exposes S3 API on `:3900`. MinIO is S3-compatible.

| Option | Pros | Cons |
|---|---|---|
| **Reuse Garage** | No new container; existing secrets (`GARAGE_ACCESS_KEY`/`GARAGE_SECRET_KEY`); single object-store pane | Garage's S3 API coverage may not match every MinIO feature meeting-api uses (e.g., multipart, presigned GET for audio downloads). `MINIO_ENDPOINT=garage:3900` with `MINIO_SECURE=false` should work for Put/Get/List — needs validation |
| **New MinIO** | Known-good upstream default; drop-in | Extra container; duplicate object-store surface; more ops |

**Recommendation**: Reuse Garage. Point `MINIO_ENDPOINT=garage:3900`, `MINIO_ACCESS_KEY`/
`MINIO_SECRET_KEY` to Garage keys, create a dedicated bucket `vexa-recordings`. Recording is
disabled by default anyway (GDPR), so this path is exercised only when RECORDING_ENABLED=true
is flipped for ops/debug. Finalise with an integration check before go-live (see plan Phase 2).

### 5.2 Postgres: dedicated vs shared

Klai's main postgres runs `pgvector/pgvector:pg18` (pg18 with pgvector extension). Upstream
targets `postgres:17-alpine` with `idle_in_transaction_session_timeout=60000`.

| Option | Pros | Cons |
|---|---|---|
| **Dedicated `vexa-postgres` pg17** | Exact upstream parity; isolated blast radius; easy drop | Second postgres instance; extra memory; backups separate |
| **Shared klai postgres (pg18) with `vexa` DB** | Single postgres; existing backup; extant `vexa` DB already there (from VEXA-001) | pg18 vs pg17 major-version skew (low risk — meeting-api uses asyncpg; no pg18-incompatible features are known upstream); cannot set `idle_in_transaction_session_timeout` per-DB |

**Recommendation**: Share klai postgres (pg18). The `idle_in_transaction_session_timeout`
is a defence-in-depth mitigation; klai postgres can set the same value cluster-wide or
via `ALTER DATABASE vexa SET ...`. Major-version skew (pg17 → pg18) is backwards-compatible
at the wire protocol and SQL surface Vexa meeting-api uses. Wipe the `vexa` database
during migration (explicitly authorised by user).

### 5.3 Redis: version — keep redis:8-alpine (forward-compatibility policy)

Klai's main redis is `redis:8-alpine`. Upstream Vexa main compose pins `redis:7.0-alpine`.
The dedicated `vexa-redis` block on core-01 currently runs `redis:8-alpine`.

**User policy**: "we don't downgrade if the new version works with the new stack".
Only downgrade if an explicit compatibility test fails.

| Option | Pros | Cons |
|---|---|---|
| **Keep `redis:8-alpine` (chosen)** | Consistent major version across klai stack (main redis + vexa-redis both on 8); no downgrade; ops burden reduced | Minor drift from upstream Vexa compose (pinned at 7.0); forward-compat risk points need validation |
| **Downgrade to `redis:7.0-alpine`** | Exact upstream parity | Violates "don't downgrade if the new version works" policy; two redis major versions in production would also diverge if we later upgraded main redis |

**Conclusion**: Keep `redis:8-alpine`. Redis 8 is backward-compatible with the RESP protocol
and Redis 7 client libraries used by Vexa meeting-api (redis-py) and runtime-api. Validate
empirically before committing — see Risk below.

**Forward-compatibility risk points to validate (Phase 7 sub-step):**

1. **ACL defaults**: Redis 8 tightened some default ACLs. Vexa uses the legacy
   `requirepass` password (no ACL users), so this should be a no-op — confirm by connecting
   with the configured password and running `CLIENT INFO` and `AUTH`.
2. **AOF (append-only file) format**: Redis 8 uses a multi-part AOF format by default
   (introduced in 7.0). Since `vexa-redis-data` is a new volume in this migration, there is
   no legacy AOF to upgrade — format compatibility is a non-issue on first boot.
3. **Pub/Sub protocol**: RESP-level pub/sub is stable across 7 → 8. Vexa meeting-api
   subscribes to transcription streams and publishes bot state; no RESP-3-specific features
   are used. Confirm by running an end-to-end pub/sub test (Phase 7) — publish from
   meeting-api, subscribe from runtime-api or the bot container.
4. **Streams (`XADD`, consumer groups)**: Vexa uses Redis streams for transcription
   ingestion. Stream semantics are stable from 5.0 onward; 7 → 8 changed nothing that
   Vexa relies on. Confirm via a real meeting flow that produces stream entries and that
   consumer groups read them correctly.
5. **Client libraries**: `redis-py` (meeting-api) and whatever runtime-api uses — both
   support 7 and 8. `appendfsync everysec` flag is still valid in 8.
6. **Eviction policy defaults**: Unchanged (`noeviction` by default). Vexa's caches fit
   within the `256M` container limit at expected load.

**Rollback path** (documented in plan §7 substep): a single-line compose change
(`image: redis:8-alpine` → `image: redis:7.0-alpine`) and a restart. Since `vexa-redis-data`
is new on first boot of this migration, no data conversion is needed. Snapshot the volume
immediately before rollback so consumer-group cursors are preserved.

### 5.4 Optional services (dashboard / mcp / tts-service)

| Service | Recommendation | Rationale |
|---|---|---|
| `dashboard` | **OPT-OUT for v1** | klai-portal covers the UI; Caddy subdomain + JWT_SECRET config is extra work without a clear win in test-mode. Can be added later |
| `mcp` | **OPT-OUT for v1** | klai-knowledge-mcp already covers Klai's MCP surface; adding vexa's mcp offers duplicate tooling. Revisit if the tools diverge |
| `tts-service` | **OPT-OUT for v1** | Speak-in-meeting is not in Klai's roadmap; `TTS_SERVICE_URL` stays empty on meeting-api |

Decision can be reversed without re-running the migration — they are independent services.

### 5.5 Scribe transcription path

`tier` parameter IS available upstream (verified §3.7). Scribe migration therefore:

1. Flip `WHISPER_SERVER_URL` stays `http://172.18.0.1:8000` (we retain port 8000 on gpu-01
   for tunnel stability; the transcription-service's nginx LB listens on 80 internally,
   and we port-map `127.0.0.1:8000:80` on gpu-01 to preserve the consumer URL)
2. `WhisperHttpProvider.transcribe()` adds form field `transcription_tier=deferred`
3. Handle 503 responses with `Retry-After` — new retry wrapper in `WhisperHttpProvider`
4. Response schema is OpenAI-compatible and matches what Scribe already consumes
   (`text`, `language`, `duration`, `inference_time_seconds`)

No queue in front of transcription-service is needed: the admission-control model already
gives realtime slots priority. `REALTIME_RESERVED_SLOTS=1` reserves one slot for meetings;
with `MAX_CONCURRENT_TRANSCRIPTIONS=20` Scribe gets up to 19 slots concurrently.

### 5.6 Network topology

```
Caddy (reverse proxy, klai-net) —— tenant.getklai.com ——→ portal-api (klai-net)
                                                              │
                                                              │  httpx (X-API-Key + X-Request-ID)
                                                              ▼
  ┌─────────────────────────── klai-net ──────────────────────┼─────────────────────────┐
  │                                                           │                         │
  │     api-gateway (8000, maps :8056 external if needed)  ◄──┘                         │
  │          │                                                                           │
  │          ├──→ meeting-api (8080)                                                     │
  │          ├──→ admin-api (8001)                                                       │
  │          └──→ mcp (18888) [opt]                                                      │
  │                                                                                      │
  │     meeting-api ──(net-postgres)──→ klai postgres (vexa DB)                          │
  │     meeting-api ──(net-redis)─────→ vexa-redis                                       │
  │     meeting-api ──(klai-net)──────→ portal-api:/api/bots/internal/webhook            │
  │     meeting-api ──(vexa-bots)─────→ bot containers (for transcription callbacks)     │
  │                                                                                      │
  │     runtime-api ──(socket-proxy)──→ docker-socket-proxy                              │
  │     runtime-api ──(vexa-bots)─────→ bot containers (spawn/stop)                      │
  └──────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           │  ssh tunnel (gpu-tunnel.service)
                                           ▼
                          gpu-01 (172.18.0.1:8000 on core-01's view)
                          transcription-service nginx (port 80 container → 8000 host)
                             ├── worker-1 (cuda)
                             └── worker-2 (cuda)
```

Notes:

- Klai keeps portal-api → meeting-api direct on `klai-net`, **bypassing api-gateway**
  for existing call sites (no behaviour change). We still deploy api-gateway because
  the dashboard/mcp flows expect it AND new features may land there first.
- `vexa-bots` network retains internet egress (not `internal: true`). Bots must reach
  Google Meet / Teams.
- Bot containers call back to meeting-api via `MEETING_API_URL=http://meeting-api:8080`
  which resolves on `vexa-bots` (meeting-api has multiple network memberships).
- `ALLOW_PRIVATE_CALLBACKS=1` stays set on runtime-api (bot callbacks land on a private
  Docker IP).

---

## 6. Authentication and secrets model

### 6.1 Secret inventory

| Secret | Purpose | Where set |
|---|---|---|
| `VEXA_API_KEY` | API key used by portal-api client → meeting-api (X-API-Key) | Already in SOPS; stays |
| `VEXA_ADMIN_TOKEN` | admin-api `ADMIN_API_TOKEN`; meeting-api `ADMIN_TOKEN`; dashboard `VEXA_ADMIN_API_KEY` | SOPS; re-use existing |
| `INTERNAL_API_SECRET` | shared secret for admin-api ↔ api-gateway ↔ meeting-api internal calls | **NEW** — `openssl rand -hex 32` |
| `BOT_API_TOKEN` | runtime-api `API_KEYS` + `RUNTIME_API_TOKEN` on meeting-api — bot callback auth | **NEW** — `openssl rand -hex 32` |
| `VEXA_DB_PASSWORD` | klai postgres password for `vexa` user | Already in SOPS; stays |
| `VEXA_REDIS_PASSWORD` | redis AUTH for vexa-redis | Already in SOPS; stays |
| `VEXA_WEBHOOK_SECRET` | portal-api bearer auth on `/api/bots/internal/webhook` | Optional; keep if set |
| `JWT_SECRET` | dashboard JWT (only if dashboard shipped) | Deferred |
| `TTS_API_TOKEN` | tts-service token (only if tts shipped) | Deferred |

### 6.2 Auth flow changes

- **Portal-api → meeting-api**: continue with `X-API-Key: {VEXA_API_KEY}`, direct on klai-net
- **Meeting-api → runtime-api**: via `RUNTIME_API_TOKEN` (new) — upstream added token
  auth between the two services
- **Bot → meeting-api callback**: via `BOT_API_TOKEN` (new); runtime-api injects it into
  bot container env
- **Meeting-api → portal-api webhook**: unchanged — source-IP allowlist (172.x), optional
  Bearer via `VEXA_WEBHOOK_SECRET`; HMAC-SHA256 path deferred (see §3.5)

---

## 7. Image tagging + build pipeline for Klai

Upstream `BUILD_TAG = $(VERSION)-$(shell date +%y%m%d-%H%M)`. VERSION file on upstream is
`0.10.0`. Klai will:

1. On core-01, `git clone` our fork at `klai/main-YYMMDD-<sha>` branch
2. Run `make build` (upstream's wrapper) — produces e.g. `vexaai/admin-api:0.10.0-260419-1530`
3. Re-tag with `klai` suffix locally to make the origin explicit:
   `vexaai/admin-api:0.10.0-260419-1530-klai` (optional — simpler to keep upstream tag)
4. Pin the full tag in `deploy/docker-compose.yml` for every vexa image
5. NEVER use `:latest`, `:dev`, `:staging` in the deployed compose
6. `deploy/VERSIONS.md` records tags alongside git SHA and build date for audit

Pre-commit check (new):

```bash
# deploy/check-image-tags.sh — fails if any vexa service uses :latest / :dev / :staging
grep -nE 'vexaai/[a-z-]+:(latest|dev|staging)\b' deploy/docker-compose.yml && exit 1
```

Wire into git pre-commit hook.

---

## 8. Migration risks and references

| Risk | Reference | Mitigation |
|---|---|---|
| `:latest` drift repeats VEXA-001 incident | SPEC-VEXA-001 §7 Fault 1 | Pre-commit grep check + explicit SPEC exclusion |
| Architecture redesign during migration | `pitfalls/process-rules.md` § no-architecture-change-in-migration | This SPEC IS the clean-slate scope; each block re-reviews service boundaries, no "improvements" added |
| Silent scribe data loss | user constraint | Hard exclusion list in migration scripts; pre/post row counts and volume file counts |
| Fork fix rebase divergence | §4 above | Semantic port with manual review; functional validation in acceptance (3-person Google Meet) |
| GPU VRAM on transcription-service | SPEC-VEXA-002 §5 | Same model (large-v3-turbo ~3GB); validate with `nvidia-smi` post-deploy |
| SSH tunnel port change breaks consumers | `.claude/rules/klai/infra/servers.md` | Retain port 8000 on gpu-01 host — map container `80` → host `8000` |
| postgres pg17 vs pg18 drift | §5.2 | Low — protocol/API compatible; SQL used is standard |
| redis 7 vs 8 drift (upstream wants 7, we keep 8) | §5.3 | Forward-compatibility test in Phase 7; single-line rollback if any test fails |
| Webhook shape change | §3.5 | Current portal-api handler already supports envelope via `model_validator(mode="before")`; add test fixtures for upstream envelope |
| Orphaned `vexa-meeting-api:klai` / `vexa-runtime-api:klai` images | plan Phase 8 | `docker save` backup before prune; retain 30 days |

---

## 9. Answers to research questions

| Q | Answer |
|---|---|
| Does tier=realtime/deferred exist on upstream main? | **Yes** — `_normalize_transcription_tier()`, `_deferred_capacity_available()`, form field `transcription_tier` + header `X-Transcription-Tier`, 503 with `Retry-After` when deferred is out of capacity |
| Current webhook payload shape | Envelope `{event_id, event_type, api_version, created_at, data:{meeting:{...}}}` — klai handler already supports both envelope and flat via `model_validator(mode="before")` |
| Does upstream enforce HMAC on `POST_MEETING_HOOKS`? | No — HMAC-SHA256 applies only to per-client webhooks (user-configured). Internal hooks rely on network reachability. Klai keeps source-IP allowlist |
| What env var controls HMAC signing? | `meeting.data.webhook_secret` (per-client, set via `PUT /user/webhook` on admin-api). For internal `POST_MEETING_HOOKS`, there is no env-var-level signing secret |
| Profile schema upstream vs klai | Deltas in §3.3: upstream adds `command`, `working_dir`, `ports`, `env`, flips `auto_remove=false`, doubles `shm_size`, `idle_timeout=0`, uses `${BROWSER_IMAGE}` placeholder |
| Does `fix/remove-whisperlive` remote branch help us? | Exists on `origin` remote; examined but not targeted — we go direct to upstream/main which already removed WhisperLive |
| Is `experimental/youtube_import` relevant? | No — YouTube ingest is not in Klai scope |

---

## 10. Tracebacks to prior SPECs

- **SPEC-VEXA-001** (DONE): 17 commits migrated the meeting bot to `feature/agentic-runtime` @
  600cba04. This SPEC **supersedes** VEXA-001 (status → `completed-superseded-by-VEXA-003`).
  The first-attempt post-mortem in VEXA-001 §7 supplies the pitfalls guardrail list for
  VEXA-003 Phase 6.
- **SPEC-VEXA-002** (DRAFT, never started): transcription-service migration on gpu-01.
  **Folded into** this SPEC (status → `cancelled-folded-into-VEXA-003`). The tier=deferred
  admission model and 503+Retry-After handling carry over verbatim.

---

End of research.md.
