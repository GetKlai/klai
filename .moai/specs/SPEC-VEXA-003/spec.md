---
id: SPEC-VEXA-003
version: "1.1"
status: deployed-awaiting-e2e
created: 2026-04-19
updated: 2026-04-19
author: MoAI
priority: high
supersedes: SPEC-VEXA-001
folds_in: SPEC-VEXA-002
deployed_commits:
  - f3fbe275  # SPEC docs
  - cde34810  # Phase 1+3 deploy manifests
  - 84ec02cb  # Phase 4+5 portal webhook + scribe tier=deferred
  - 484fb816  # Phase 6 image tag pins
  - 9155355e  # deploy-notes runtime findings
  - 38efe04e  # defense-in-depth API_KEYS
outstanding_gate: Real Google Meet E2E test (acceptance §I — audio + transcript round-trip) pending user-initiated Meet URL. All synthetic checks pass.
---

# SPEC-VEXA-003: Clean-Slate Rebuild on Upstream Vexa main (v0.10)

## HISTORY

| Version | Date       | Author | Change |
|---------|------------|--------|--------|
| 1.0     | 2026-04-19 | MoAI   | Initial SPEC — clean-slate rebuild, supersedes VEXA-001, folds in VEXA-002 |

---

## 1. Context

Klai currently runs a Vexa meeting bot + transcription stack in production test-mode:

- `vexa-meeting-api:klai` + `vexa-runtime-api:klai` — locally built from the abandoned
  `feature/agentic-runtime` branch at commit `600cba04` (SPEC-VEXA-001 delivered)
- `vexa-redis` (`redis:8-alpine`) on the isolated `vexa-bots` network
- `vexaai/vexa-bot:v20260315-2220` (ephemeral, spawned by runtime-api)
- Custom `whisper-server` (`ghcr.io/getklai/whisper-server:latest`) on gpu-01 — single
  asyncio lock, no tier, no VAD, no hallucination detection

Upstream Vexa has moved on. `feature/agentic-runtime` is abandoned; the current
`upstream/main` (v0.10 track) represents a modular production release with durable
webhook retry, tier-aware transcription admission control, Redis-backed idempotency,
and a standardised image tagging pipeline.

User explicitly approved a clean-slate rebuild: Klai is in test mode, no paying
customers, things may break. The `vexa` database may be wiped. The `scribe-audio-data`
volume and scribe postgres tables must remain untouched — these contain real meeting
recordings we want to keep.

This SPEC supersedes VEXA-001 and folds in VEXA-002 (whisper-server replacement),
shipping both as one migration.

### 1.1 Related work

- **SPEC-VEXA-001** (status `completed`) — the current prod deploy. Post-mortem in §7
  of that SPEC documents the `:latest` pitfall and the "architecture-change during
  image swap" failure mode. VEXA-003 reapplies every guardrail from that post-mortem.
- **SPEC-VEXA-002** (status `draft`, never implemented) — transcription-service swap.
  Folded into this SPEC unchanged.
- **`.claude/rules/klai/pitfalls/process-rules.md`** — enforced: `spec-discipline`,
  `no-architecture-change-in-migration`, `verify-changes-landed`, `data-before-code`.

---

## 2. Requirements (EARS Format)

### 2.1 Ubiquitous Requirements

**[REQ-U-001]** The system SHALL process all meeting audio and transcription exclusively
on EU infrastructure (core-01 Hetzner Falkenstein, gpu-01 Hetzner Falkenstein).

**[REQ-U-002]** The system SHALL use explicit immutable image tags of the form
`<version>-YYMMDD-HHMM` (upstream convention) for every Vexa service in
`deploy/docker-compose.yml` and `deploy/docker-compose.gpu.yml`. Mutable tags
(`:latest`, `:dev`, `:staging`) SHALL NOT appear in deploy files.

**[REQ-U-003]** The `scribe-audio-data` Docker volume and scribe postgres tables SHALL
remain unchanged (same row counts, same byte count) from the moment of pre-migration
snapshot until the moment of post-migration verification.

**[REQ-U-004]** Secrets (`VEXA_API_KEY`, `VEXA_ADMIN_TOKEN`, `INTERNAL_API_SECRET`,
`BOT_API_TOKEN`, `VEXA_DB_PASSWORD`, `VEXA_REDIS_PASSWORD`, `VEXA_WEBHOOK_SECRET`) SHALL
be stored in the SOPS-encrypted `/opt/klai/.env` file and SHALL NOT appear in plaintext
in any committed file under `deploy/`.

**[REQ-U-005]** The system SHALL propagate `X-Request-ID` from Caddy through portal-api
to every Vexa service call (`get_trace_headers()` from `app.trace`) so a single
`request_id:<uuid>` query in VictoriaLogs returns the full chain.

**[REQ-U-006]** The `vexa-redis` service SHALL run `redis:8-alpine` to maintain
consistency with klai's main redis. Rationale: "we don't downgrade if the new version
works with the new stack". If the forward-compatibility tests defined in acceptance.md
fail, the decision is revisited per research.md §5.3, and the rollback to
`redis:7.0-alpine` is a single-line compose change.

### 2.2 Event-Driven Requirements

**[REQ-E-001]** WHEN portal-api POSTs a bot request to `meeting-api` (via direct
klai-net hop on port 8080 with `X-API-Key: {VEXA_API_KEY}`), THE SYSTEM SHALL
provision a `vexa-bot` container via runtime-api and return a created meeting record
with a `native_meeting_id` and initial status within 5 seconds.

**[REQ-E-002]** WHEN a meeting ends (host leaves, bot times out, or portal-api
issues DELETE), THE `meeting-api` SHALL fire `meeting.completed` webhook to
`${POST_MEETING_HOOKS}` (= `http://portal-api:8010/api/bots/internal/webhook`)
**exactly once** per meeting, per the April 18 dedup fix
(commit `19cff9d` — status path no longer double-fires `meeting.completed`).

**[REQ-E-003]** WHEN a webhook delivery fails (non-2xx or transport error), THE
`meeting-api` SHALL enqueue the delivery to the Redis-backed retry queue
(`webhook:retry_queue`) and retry with exponential backoff via the
`webhook_retry_worker` background task.

**[REQ-E-004]** WHEN the meeting bot streams audio for transcription, THE bot
container SHALL call the transcription-service with `transcription_tier=realtime`
(form field) OR `X-Transcription-Tier: realtime` (header).

**[REQ-E-005]** WHEN `scribe-api` uploads an audio file for transcription via
`WhisperHttpProvider`, THE call SHALL include `transcription_tier=deferred`.

**[REQ-E-006]** WHEN the transcription-service returns HTTP 503 with `Retry-After`
header for a deferred request, THE `WhisperHttpProvider` SHALL wait the indicated
seconds (clamped to `[1, 30]`) and retry with exponential backoff up to 3 attempts.

**[REQ-E-007]** WHEN portal-api receives a request at `/api/bots/internal/webhook`
AND the caller source IP is outside the internal Docker ranges (`172.x`, `10.x`,
`192.168.x`) AND `VEXA_WEBHOOK_SECRET` is set, THE portal-api SHALL reject the
request with 401 unless the `Authorization: Bearer {VEXA_WEBHOOK_SECRET}` header
matches.

**[REQ-E-008]** WHEN a pre-commit hook detects an image reference matching the
pattern `vexaai/[a-z-]+:(latest|dev|staging)\b` in any file under `deploy/`, THE
commit SHALL be rejected with a message pointing to REQ-U-002.

### 2.3 State-Driven Requirements

**[REQ-S-001]** WHILE any Vexa service runs (admin-api, api-gateway, meeting-api,
runtime-api, mcp if enabled, tts-service if enabled, dashboard if enabled,
transcription-service, vexa-redis), it SHALL expose a `/health` endpoint that
returns a JSON status document within 5 seconds.

**[REQ-S-002]** WHILE a bot container is active, THE `meeting-api` SHALL track
its lifecycle (`joining`, `awaiting_admission`, `active`, `completed`, `failed`)
in the `meetings` table of the `vexa` database AND propagate status changes to
portal-api via webhook.

**[REQ-S-003]** WHILE no meetings are active, THE system SHALL have zero
ephemeral `vexa-bot` containers running (verified by `docker ps --filter
ancestor=vexaai/vexa-bot:<tag>`).

**[REQ-S-004]** WHILE the transcription-service runs, its `/health` endpoint
SHALL report current capacity: `active_realtime`, `active_deferred`,
`max_concurrent`, `realtime_reserved_slots`, `deferred_capacity_available`.

### 2.4 Unwanted Behavior Requirements

**[REQ-N-001]** IF any Vexa service definition in `deploy/docker-compose.yml`
mounts the `scribe-audio-data` volume OR references a scribe postgres table,
THEN the deploy SHALL fail fast with an explicit error before any container starts.

**[REQ-N-002]** IF `meeting-api` or `api-gateway` is unreachable from portal-api,
THEN portal-api SHALL surface a clear user-facing error, set the `VexaMeeting`
row status to `failed` with a descriptive `error_message`, and SHALL NOT crash
the portal-api process.

**[REQ-N-003]** IF a bot container crashes or fails to join a meeting within
`max_wait_for_admission` (120_000 ms), THEN `meeting-api` SHALL mark the meeting
`failed` and fire the `bot.failed` webhook event type.

**[REQ-N-004]** IF the `vexa-postgres` database contains the old VEXA-001 schema
at the start of migration, THEN the migration script SHALL drop the database and
recreate it from scratch (explicit data loss, user-authorised).

**[REQ-N-005]** IF any build script, migration script, or deploy command uses
`:latest`, `:dev`, or `:staging` as the image tag for a Vexa service, THEN the
script SHALL exit non-zero and the deploy SHALL NOT proceed.

**[REQ-N-006]** IF the upstream image build produces a tag that does not match
the pattern `^[0-9]+\.[0-9]+\.[0-9]+-[0-9]{6}-[0-9]{4}$`, THEN the build script
SHALL fail.

### 2.5 Optional Feature Requirements

**[REQ-O-001]** WHERE the `dashboard` service is deployed, THE dashboard SHALL be
reachable at a dedicated subdomain under `${DOMAIN}` (e.g. `vexa.${DOMAIN}`) via
Caddy reverse proxy, authenticated through the existing Klai auth layer or
Vexa's own JWT_SECRET path.

**[REQ-O-002]** WHERE the `mcp` service is deployed, THE Klai agent stack MAY
consume its tools via `MCP_URL=http://mcp:18888`; this is NOT required for v1.

**[REQ-O-003]** WHERE the `tts-service` is deployed, THE meeting-api `TTS_SERVICE_URL`
MAY point at it for future speak-in-meeting functionality; this is NOT required for v1.

**[REQ-O-004]** WHERE per-client HMAC-SHA256 webhook signing is required for
future external integrations, THE system SHALL use the upstream
`PUT /user/webhook` + `meeting.data.webhook_secret` path — this is documented
future work and NOT in scope for v1.

---

## 3. Architecture

### 3.1 New service topology on core-01 (klai-net)

| Service | Image | Port (container:host) | Networks | Memory |
|---|---|---|---|---|
| `admin-api` | `vexaai/admin-api:<tag>` | 8001:- (internal) | klai-net | 256M |
| `api-gateway` | `vexaai/api-gateway:<tag>` | 8000:- (internal) | klai-net | 1G |
| `meeting-api` | `vexaai/meeting-api:<tag>` | 8080:- (internal) | klai-net, net-postgres, vexa-bots | 1G |
| `runtime-api` | `vexaai/runtime-api:<tag>` | 8090:- (internal) | vexa-bots, socket-proxy | 256M |
| `vexa-redis` | `redis:8-alpine` | internal | vexa-bots, net-redis | 256M |
| `mcp` (opt) | `vexaai/mcp:<tag>` | 18888:- (internal) | klai-net | 512M |
| `dashboard` (opt) | `vexaai/dashboard:<tag>` | 3000:- (internal, Caddy-routed) | klai-net | 1G |
| `tts-service` (opt) | `vexaai/tts-service:<tag>` | 8002:- (internal) | klai-net | 1G |
| `vexa-bot` (ephemeral) | `vexaai/vexa-bot:<tag>` | — | vexa-bots | 1536Mi |

Optional services (`mcp`, `dashboard`, `tts-service`) are NOT shipped in v1 per
research.md §5.4. They may be added later without re-running this migration.

### 3.2 Services on gpu-01

| Service | Image | Port | Notes |
|---|---|---|---|
| `transcription-worker-1` | built from `services/transcription-service/Dockerfile` (CUDA 12.3.2) | internal | faster-whisper large-v3-turbo, int8 compute |
| `transcription-worker-2` | same image | internal | second worker for concurrency |
| `transcription-api` | `nginx:alpine` with `nginx.conf` from upstream | `127.0.0.1:8000:80` | preserves existing consumer URL via SSH tunnel |

Port mapping `127.0.0.1:8000:80` on gpu-01 retains the existing `gpu-tunnel.service`
tunnel unchanged. Consumers on core-01 continue to call `http://172.18.0.1:8000`.

### 3.3 Services removed

| Removed | Replaced by |
|---|---|
| `vexa-meeting-api:klai` (locally built, `feature/agentic-runtime` @ 600cba04) | `vexaai/meeting-api:<tag>` + `vexaai/runtime-api:<tag>` + `vexaai/admin-api:<tag>` + `vexaai/api-gateway:<tag>` |
| `ghcr.io/getklai/whisper-server:latest` on gpu-01 | `services/transcription-service/` (nginx + 2 CUDA workers) |

### 3.4 Shared Klai services re-used

| Klai service | Role in Vexa |
|---|---|
| klai postgres (`pgvector/pgvector:pg18`) | hosts `vexa` database (separate DB in same cluster); `ALTER DATABASE vexa SET idle_in_transaction_session_timeout = 60000` applied |
| Garage (S3-compatible, `dxflrs/garage:v2.3.0`) | object store for recordings when `RECORDING_ENABLED=true`. `MINIO_ENDPOINT=garage:3900`, dedicated bucket `vexa-recordings` |
| Caddy | reverse proxy (optional dashboard subdomain only — portal-api → meeting-api stays internal) |
| docker-socket-proxy | runtime-api container spawning |
| portal-api | orchestrator, multi-tenant scoping, UI |
| gpu-tunnel.service | SSH tunnel core-01 → gpu-01 transcription-service on port 8000 |

### 3.5 Data flow

```
1.  User → portal-api        : "Start meeting bot" {meeting_url, group_id, consent_given}
2.  portal-api → meeting-api : POST /bots {platform, native_meeting_id, recording_enabled=false, bot_name="Klai", automatic_leave}
                               Headers: X-API-Key={VEXA_API_KEY}, X-Request-ID, X-Org-ID
3.  meeting-api → runtime-api: POST /containers  (with profiles.yaml "meeting" profile, BROWSER_IMAGE)
4.  runtime-api → docker     : create + start vexa-bot container on vexa-bots network
5.  vexa-bot → Google Meet   : Playwright join
6.  vexa-bot → transcription : POST /v1/audio/transcriptions  transcription_tier=realtime
                               (via TRANSCRIPTION_SERVICE_URL=http://172.18.0.1:8000/v1/audio/transcriptions)
7.  transcription → vexa-bot : OpenAI-compatible JSON {text, segments, language, duration}
8.  vexa-bot → meeting-api   : POST /internal/transcripts  (segments + speaker events)
9.  Meeting ends             : bot exits → runtime-api reaps (auto_remove=false → meeting-api finalises)
10. meeting-api fires:
    - send_completion_webhook → {event_id, event_type="meeting.completed", data:{meeting:{...}}}
    - POST_MEETING_HOOKS      → same envelope, exactly once
11. portal-api → /api/bots/internal/webhook → transition VexaMeeting status → "completed"
    → emit product_event "meeting.completed"
```

### 3.6 Image tagging strategy

Per research.md §7:

- Immutable tags: `<version>-YYMMDD-HHMM` (e.g. `0.10.0-260419-1530`)
- `deploy/docker-compose.yml` pins every `vexaai/*` image to a specific
  immutable tag
- `deploy/VERSIONS.md` records the tag + git SHA + build date
- Pre-commit check in `deploy/check-image-tags.sh`:

  ```bash
  #!/bin/sh
  set -e
  if grep -nE 'vexaai/[a-z-]+:(latest|dev|staging)\b' deploy/docker-compose.yml deploy/docker-compose.gpu.yml 2>/dev/null; then
    echo "ERROR: Vexa service uses mutable tag. See REQ-U-002 in SPEC-VEXA-003." >&2
    exit 1
  fi
  ```

---

## 4. Scope

### In scope

- Build Vexa images from `klai/main-YYMMDD-<sha>` branch (upstream main + rebased
  klai bot fixes) on core-01 using upstream's `make build` pipeline
- Deploy: `admin-api`, `api-gateway`, `meeting-api`, `runtime-api`, `vexa-redis`
  on core-01 (six services; optional mcp/dashboard/tts deferred)
- Deploy: `transcription-service` (nginx + 2 workers) on gpu-01, replacing
  `whisper-server`
- Rebase `fdb751f` (participant registry MutationObserver) and `787e517` (video
  block WebRTC stability) onto upstream main, or skip with justification if
  already fixed upstream
- Update portal-api:
  - Point at new meeting-api (direct on klai-net, port 8080) — `vexa_meeting_api_url`
    already correct
  - Ensure webhook handler parses upstream envelope — already supports via
    `VexaWebhookPayload._normalize`
  - Add optional HMAC verification path (Bearer token flow; HMAC deferred per REQ-O-004)
- Update scribe-api `WhisperHttpProvider`:
  - Add `transcription_tier=deferred` form field
  - Add 503 + Retry-After handling with exponential backoff (≤ 3 retries)
  - Preserve test audio + scribe postgres tables unchanged
- Wipe and recreate the `vexa` database in klai postgres
- Adopt upstream image tagging convention in deploy files
- Add pre-commit `check-image-tags.sh`
- SOPS-encrypt new env vars (`INTERNAL_API_SECRET`, `BOT_API_TOKEN`)
- Update `deploy/vexa/profiles.yaml` to upstream schema (2GB shm, `${BROWSER_IMAGE}`,
  `auto_remove=false`, `idle_timeout=0`)
- Update `deploy/VERSIONS.md`
- Backup old images (`docker save` for `vexa-meeting-api:klai` + `vexa-runtime-api:klai`)
  before pruning

### Out of scope

- Paying-customer data migration (no paying customers)
- Vexa `agent-api` (Claude Code CLI runner — Klai uses its own agent stack)
- Vexa `telegram-bot`, `calendar-service` (not shipping upstream either in 0.10)
- HMAC-SHA256 webhook signing for internal `POST_MEETING_HOOKS` (deferred)
- WebSocket real-time transcript streaming to frontend
- Vexa's own user/tenant management via `admin-api` CRUD endpoints — Klai uses
  Zitadel for users and keeps its own `VexaMeeting` per-org scoping. `admin-api`
  is deployed to keep the api-gateway auth contract working but Klai does not
  call its user CRUD
- `dashboard`, `mcp`, `tts-service` (deferred to post-v1)
- New GPU hardware
- Zoom platform support (upstream main is 0/100 confidence for Zoom)

### Exclusions (What NOT to Build)

- **No** architectural "improvements" while migrating. Per
  `no-architecture-change-in-migration` pitfall, the migration copies upstream
  topology as-is. Exception: three services are deliberately omitted
  (mcp/dashboard/tts-service) and one is deliberately downstream-retained
  (portal-api → meeting-api direct hop bypassing api-gateway). These are
  documented deviations in §3.1 and §5.
- **No** changes to scribe ingestion flow, scribe postgres tables, scribe audio
  retention policy, scribe summariser, or any non-whisper component of scribe-api
- **No** Caddy config changes unless dashboard is adopted (it is not, for v1)
- **No** changes to `klai-knowledge-mcp` or `klai-agent-stack`
- **No** renames of existing docker volumes, networks, or secrets — only new
  additions (`INTERNAL_API_SECRET`, `BOT_API_TOKEN`)
- **No** redis version downgrade from 8 to 7 unless forward-compat tests fail (see REQ-U-006)

---

## 5. Technical Constraints

- **Image source**: Build from our fork branch `klai/main-YYMMDD-<sha>` (upstream
  main + rebased bot fixes). Pin `deploy/docker-compose.yml` to the exact
  resulting timestamped tag. Never `:latest`
- **Server**: Build runs on core-01 (Hetzner EX44). GPU images build on core-01
  then `docker save` / `docker load` to gpu-01, OR build directly on gpu-01
  (CUDA base image requires only Docker, no GPU at build time)
- **Docker GID**: runtime-api `group_add: 988` on core-01 (Docker daemon GID)
- **Network**: bot containers must have internet egress (`vexa-bots` network
  must NOT be `internal: true`). meeting-api must be on `vexa-bots` AND
  `klai-net` AND `net-postgres` (multi-homed)
- **Postgres**: `vexa` database in klai's `pgvector/pgvector:pg18`, wiped and
  recreated. `ALTER DATABASE vexa SET idle_in_transaction_session_timeout = 60000`
- **Redis**: dedicated `vexa-redis` at `redis:8-alpine` (same as klai main redis;
  forward-compat policy — see research.md §5.3 and REQ-U-006). Password retained
  via `VEXA_REDIS_PASSWORD`. Rollback to `redis:7.0-alpine` is a single-line
  compose change if Phase 7 forward-compat tests fail
- **Object store**: Garage on `klai-net`. `MINIO_ENDPOINT=garage:3900`,
  `MINIO_SECURE=false`, bucket `vexa-recordings` (pre-created). `RECORDING_ENABLED=false`
  by default (GDPR)
- **gpu-01 port retention**: transcription nginx on host port 8000 (container 80).
  `gpu-tunnel.service` unchanged
- **Secrets**: SOPS-encrypted `/opt/klai/.env`. New additions: `INTERNAL_API_SECRET`,
  `BOT_API_TOKEN`, `VEXA_ADMIN_TOKEN` (if not already present)
- **Webhook auth**: source-IP allowlist for internal hooks; HMAC-SHA256 deferred
- **Observability**: all services stdout JSON (structlog) → Alloy → VictoriaLogs.
  `X-Request-ID` propagated via `get_trace_headers()` (see
  `.claude/rules/klai/infra/observability.md`)
- **Scribe data immutability**: `scribe-audio-data` volume and all `scribe_*`
  postgres tables read-only during migration; verified via
  file-count/row-count snapshots before and after

---

## 6. Dependencies

| Dependency | Type | Status |
|---|---|---|
| `upstream/main` of Vexa-ai/vexa | External | Active; last verified commit `f0756bf` |
| Fork branch `klai/main-YYMMDD-<sha>` | Internal | To be created in Phase 1 |
| klai postgres (pg18) | Internal | Running |
| Garage S3 | Internal | Running |
| docker-socket-proxy | Internal | Running |
| gpu-tunnel.service (core-01 → gpu-01:8000) | Internal | Running |
| SOPS / age keys | Internal | Available |
| CI via GitHub Actions (`gh run watch --exit-status`) | Internal | Required for deploy per klai-portal CLAUDE.md |

---

## 7. Guardrails carried forward from SPEC-VEXA-001 post-mortem

The first VEXA-001 implementation attempt failed by ignoring three SPEC constraints
simultaneously:

1. Used `:latest` image — REQ-U-002 + REQ-N-005 + REQ-E-008 enforce this
2. Did image-swap instead of architecture change — **this SPEC IS the architecture
   change**; plan.md Phase 3 explicitly enumerates every new service
3. Memory limits insufficient — §3.1 + plan.md §6 restate exact limits per service
4. WhisperLive ran on core-01 — transcription-service runs on gpu-01 only; meeting-api
   calls it via existing tunnel
5. Signals ignored — plan.md §6 and §7 include explicit log-based verification
   gates ("no WhisperLive process", "TRANSCRIPTION_SERVICE_URL resolved")

---

End of spec.md.
