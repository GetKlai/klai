# SPEC-VEXA-003: Acceptance Criteria — Clean-Slate Rebuild on Upstream Vexa main

> Every criterion is mechanically verifiable by a command or a counted artefact.
> No "looks correct" or "should work" claims — evidence only.

---

## A. Image tag and build discipline (REQ-U-002, REQ-N-005, REQ-E-008, REQ-N-006)

**Given** the `deploy/docker-compose.yml` and `deploy/docker-compose.gpu.yml` are committed
to the repository

**When** `bash deploy/check-image-tags.sh` runs

**Then** exit code is `0`
**And** `grep -nE 'vexaai/[a-z-]+:(latest|dev|staging)\b' deploy/docker-compose.yml deploy/docker-compose.gpu.yml` returns **zero lines**
**And** every `vexaai/*` image reference matches `^vexaai/[a-z-]+:[0-9]+\.[0-9]+\.[0-9]+-[0-9]{6}-[0-9]{4}$`
**And** `grep -n 'vexa-meeting-api:klai\|vexa-runtime-api:klai' deploy/docker-compose.yml` returns **zero lines**
**And** `deploy/VERSIONS.md` has been updated with the new `<tag> <git-sha> <build-date>` row for every Vexa service

---

## B. Scribe data immutability (REQ-U-003, REQ-N-001)

### B.1 Volume contents unchanged

**Given** the pre-migration snapshot command on core-01:

```bash
docker run --rm -v scribe-audio-data:/d alpine sh -c 'find /d -type f | wc -l; du -sb /d | cut -f1'
```

recorded **FILE_COUNT_BEFORE** and **BYTES_BEFORE**

**When** the same command is run after the migration completes

**Then** the output reports `FILE_COUNT_AFTER == FILE_COUNT_BEFORE`
**And** `BYTES_AFTER == BYTES_BEFORE` (exact byte-for-byte match)

### B.2 Scribe postgres tables unchanged

**Given** the pre-migration row count for every scribe table (captured into `reports/scribe-snapshot-pre.txt`):

```bash
docker exec postgres psql -U klai -d klai -Atc "
  SELECT tablename, n_live_tup
  FROM pg_stat_user_tables
  WHERE schemaname = 'public' AND tablename LIKE 'scribe_%'
  ORDER BY tablename;
"
```

**When** the same query is re-run after migration into `reports/scribe-snapshot-post.txt`

**Then** `diff reports/scribe-snapshot-pre.txt reports/scribe-snapshot-post.txt` returns **no differences**

### B.3 No vexa service mounts scribe volume

**Given** the post-migration `deploy/docker-compose.yml`

**When** `grep -n 'scribe-audio-data' deploy/docker-compose.yml`

**Then** the only match SHALL be on the `scribe-api` service block and the top-level
`volumes:` declaration — NO match under any `vexa*`, `meeting-api`, `runtime-api`,
`admin-api`, `api-gateway`, `mcp`, `dashboard`, `tts-service`, or `transcription-service` block

---

## C. Service inventory on core-01 (REQ-S-001, REQ-S-003)

**Given** the migration completed and `docker compose up -d` finished without error

**When** `docker ps --format '{{.Image}}\t{{.Names}}\t{{.Status}}' | grep -E 'vexaai/|vexa-'`

**Then** the output contains **exactly** these containers, all with `Status` starting with `Up` and `(healthy)`:

- `vexaai/admin-api:<tag>` → `klai-core-admin-api-1` (or equivalent name)
- `vexaai/api-gateway:<tag>` → `klai-core-api-gateway-1`
- `vexaai/meeting-api:<tag>` → `klai-core-meeting-api-1`
- `vexaai/runtime-api:<tag>` → `klai-core-runtime-api-1`
- `redis:8-alpine` → `klai-core-vexa-redis-1`

**And** NONE of these containers are present:

- `vexa-meeting-api:klai`
- `vexa-runtime-api:klai`
- any `:latest` vexa image

**And** when no meeting is active: `docker ps --filter ancestor=vexaai/vexa-bot --format '{{.Names}}'` returns **zero lines**

---

## D. Service inventory on gpu-01 (REQ-U-001)

**Given** the migration completed on gpu-01

**When** `ssh core-01 'ssh -i /opt/klai/gpu-tunnel-key root@5.9.10.215 "docker ps --format \"{{.Image}}\t{{.Names}}\""'`

**Then** the output contains:

- `nvidia/cuda:*` or project-built image → `transcription-worker-1`, `transcription-worker-2`
- `nginx:alpine` → `transcription-api`

**And** contains NO row with `ghcr.io/getklai/whisper-server`

**And** `ssh core-01 'curl -s --max-time 3 http://172.18.0.1:8000/health'` returns HTTP 200 with JSON body containing keys `active_realtime`, `active_deferred`, `max_concurrent`, `realtime_reserved_slots`

---

## E. Health endpoints (REQ-S-001)

For each service in {admin-api, api-gateway, meeting-api, runtime-api, transcription-service}:

**When** `docker exec klai-core-portal-api-1 curl -sS --max-time 5 http://<service>:<port>/health`

**Then** HTTP response is 200 within 5 seconds
**And** body is valid JSON
**And** `vexa-redis` responds to `redis-cli -a $VEXA_REDIS_PASSWORD ping` with `PONG`

---

## F. End-to-end bot flow (REQ-E-001, REQ-E-002, REQ-S-002)

### F.1 Bot provisioning

**Given** portal-api is running with `vexa_meeting_api_url=http://meeting-api:8080` and a valid
`VEXA_API_KEY` in its environment

**When** a Playwright test:
1. Logs into `https://my.getklai.com` as `playwright-test@getklai.com`
2. Navigates to the meetings page
3. Creates a Google Meet URL at `https://meet.google.com/abc-defg-hij`
4. Posts a "start bot" request via the portal UI

**Then** within 60 seconds:
- `VexaMeeting.status` transitions `pending → joining → recording` in the portal database
- `docker ps --filter ancestor=vexaai/vexa-bot:<tag>` shows **exactly 1** container
- The bot joins the meeting as a participant named "Klai"

### F.2 Transcription received within 60s of first speech

**Given** the bot has joined and a human speaks for at least 3 seconds

**When** `SELECT transcript_text FROM vexa_meetings WHERE native_meeting_id = 'abc-defg-hij'` is polled every 5 seconds

**Then** within 60 seconds of first speech, `transcript_text` is non-empty
**And** contains recognisable words (confirmed by human review of the rendered text)

### F.3 Meeting completion + webhook dedup

**Given** the human participant leaves and the bot's `max_time_left_alone` (30s) expires

**When** the bot container exits

**Then** within 2 minutes:
- `VexaMeeting.status = 'completed'`
- `portal-api` logs (query `service:portal-api AND event_type:\"meeting.completed\"`) show **exactly 1**
  emission for that `native_meeting_id`
- `emit_event("meeting.completed", ...)` fires **exactly once** (verified by
  `product_events` table: `SELECT COUNT(*) FROM product_events WHERE event_type='meeting.completed'
  AND properties->>'native_meeting_id'='abc-defg-hij'` returns `1`)

### F.4 Three consecutive meetings — no double delivery

**When** steps F.1–F.3 are repeated three times in sequence for meetings
`meet.google.com/test-001-aaa`, `test-001-bbb`, `test-001-ccc`

**Then** `SELECT native_meeting_id, COUNT(*) FROM product_events WHERE event_type='meeting.completed'
AND properties->>'native_meeting_id' IN ('test-001-aaa','test-001-bbb','test-001-ccc')
GROUP BY native_meeting_id` returns exactly three rows, each with `COUNT = 1`

---

## G. Webhook authentication (REQ-E-007)

**Given** `VEXA_WEBHOOK_SECRET` is set in portal-api env

**When** `curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-Forwarded-For: 203.0.113.42' \
  -d '{"event_type":"meeting.completed","data":{"meeting":{"platform":"google_meet","native_meeting_id":"x","status":"completed","id":1,"user_email":"x@x","user_id":1,"duration_seconds":1,"start_time":"2026-01-01T00:00:00+00:00","end_time":"2026-01-01T00:00:01+00:00","created_at":"2026-01-01T00:00:00+00:00","transcription_enabled":false}}}' \
  https://my.getklai.com/api/bots/internal/webhook`

**Then** response code is `401` (external IP, missing Bearer)

**And when** the same request is made from inside Docker (`docker exec klai-core-portal-api-1
curl ... http://localhost:8010/api/bots/internal/webhook`) without any Authorization header

**Then** response code is `200` (internal IP, allowlisted)

---

## H. Scribe tier=deferred behaviour (REQ-E-005, REQ-E-006)

### H.1 Normal deferred request

**Given** the transcription-service is up, `REALTIME_RESERVED_SLOTS=1`, `MAX_CONCURRENT_TRANSCRIPTIONS=20`

**When** scribe-api receives a real audio file upload

**Then** the resulting outbound request to `http://172.18.0.1:8000/v1/audio/transcriptions`
(verified via VictoriaLogs `service:scribe-api AND "transcription_tier=deferred"`) contains
form field `transcription_tier=deferred` OR header `X-Transcription-Tier: deferred`
**And** the response is HTTP 200 with the OpenAI-compatible JSON schema
**And** Scribe returns the transcript to the user without surfacing any 503 error

### H.2 503 retry with Retry-After

**Given** the transcription-service is artificially saturated (e.g., 20 concurrent realtime
requests generated by a load test)

**When** scribe-api posts a deferred request

**Then** the transcription-service returns 503 with `Retry-After: <1..30>`
**And** `WhisperHttpProvider` waits the indicated seconds
**And** retries up to 3 times with exponential backoff
**And** on success within 3 attempts, returns a 200 `TranscriptionResult` to the caller
**And** on total failure, raises `HTTPException(503)` — no audio data loss

### H.3 No realtime-slot starvation

**Given** Scribe is actively consuming deferred slots

**When** a new meeting bot simultaneously POSTs a realtime transcription request

**Then** the realtime request is NOT rejected (realtime slot is reserved)
**And** realtime latency (POST → 200) is < 10 seconds for a 3-second audio segment

---

## I. Local fork bugfixes present in deployed bot (functional verification)

**Given** the `klai/main-YYMMDD-<sha>` branch has been built into the bot image

**When** a 3-participant Google Meet is joined by the Klai bot for at least 2 minutes

**Then** the transcript `speaker_events` list contains at least 3 distinct speakers
(verified via portal UI meeting detail page OR `SELECT DISTINCT speaker FROM transcript_segments
WHERE meeting_id = <id>`)

**And** NO WebRTC-related `track.stop()` or transceiver renegotiation errors appear in the bot
container logs (verified by `docker logs <bot-container> 2>&1 | grep -iE "track.stop|renegotiation error"` — zero lines)

**And** the bot successfully counts participants on join and exit
(verified by `service:vexa-bot AND "participant_count"` entries in VictoriaLogs showing
the correct value)

**If the rebased cherry-picks were skipped** (because upstream already fixed the underlying
issues), the commit log of `klai/main-YYMMDD-<sha>` SHALL contain a note explaining why,
and the 3-participant test SHALL still pass on the unmodified upstream bot code.

---

## J. Database state (REQ-N-004)

**Given** the pre-migration snapshot command:

```bash
docker exec postgres psql -U klai -d vexa -Atc "SELECT COUNT(*) FROM meetings;"
```

returns some number N (authorised to discard)

**When** the migration script runs:

```bash
docker exec postgres psql -U klai -c "DROP DATABASE vexa;"
docker exec postgres psql -U klai -c "CREATE DATABASE vexa OWNER vexa;"
docker exec postgres psql -U klai -c "ALTER DATABASE vexa SET idle_in_transaction_session_timeout = 60000;"
```

**Then** post-migration `docker exec postgres psql -U klai -d vexa -Atc "SELECT COUNT(*) FROM meetings;"`
returns `0`
**And** meeting-api startup logs (`docker logs klai-core-meeting-api-1 --tail 50`) show successful
Alembic or schema init with no errors

---

## K. Secrets hygiene (REQ-U-004)

**Given** the committed state of the repository

**When** `git grep -nE 'INTERNAL_API_SECRET=[^$]|BOT_API_TOKEN=[^$]|VEXA_ADMIN_TOKEN=[^$]' -- ':!*.example' ':!*.sample'`

**Then** zero matches are returned (secrets only reference `${VAR}` placeholders, never inlined)

**And** `/opt/klai/.env` (SOPS-encrypted) contains `INTERNAL_API_SECRET`, `BOT_API_TOKEN`,
and the other new secrets — verified via
`sops -d /opt/klai/.env | grep -E '^(INTERNAL_API_SECRET|BOT_API_TOKEN)='` returning two lines
(key=value pairs, non-empty values)

---

## L. Observability (REQ-U-005)

**Given** a Playwright test meeting with request_id `RID-<uuid>` initiated from portal-api

**When** the request completes the bot-start flow and VictoriaLogs is queried
`request_id:RID-<uuid>`

**Then** log entries appear for services: `caddy`, `portal-api`, `api-gateway` (optional),
`meeting-api`, `runtime-api`, `vexa-bot` — at minimum portal-api + meeting-api +
runtime-api + the bot container
**And** each entry carries the same `request_id`
**And** `org_id` is present on portal-api + meeting-api entries

---

## M. Rollback readiness (plan.md rollback strategy)

### M.1 Image backups exist

**Given** the migration Phase 6 completes

**When** `ls -la /backup/vexa-rollback/`

**Then** two tar files exist:
- `/backup/vexa-rollback/vexa-meeting-api-600cba04-YYMMDD.tar` (≥ 50 MB)
- `/backup/vexa-rollback/vexa-runtime-api-600cba04-YYMMDD.tar` (≥ 10 MB)

**And** each can be reloaded via `docker load -i <file>` (dry-run: `docker image inspect
$(docker load -q -i <file> | awk '{print $3}')` returns valid metadata)

### M.2 Rollback dry-run

**When** the rollback playbook is dry-run on a staging copy:

```bash
docker load -i /backup/vexa-rollback/vexa-meeting-api-600cba04-YYMMDD.tar
docker image inspect vexa-meeting-api:klai
```

**Then** exit code is 0 for both commands, confirming the tar is intact and the tag re-appears

---

## N. Redis 8 forward-compatibility (REQ-U-006)

> If any sub-criterion in this section fails, the decision to keep `redis:8-alpine` is
> revisited per research.md §5.3. Rollback is a single-line compose change
> (`image: redis:8-alpine` → `image: redis:7.0-alpine`) followed by
> `docker compose up -d vexa-redis`.

### N.1 Container starts with configured password

**Given** `deploy/docker-compose.yml` pins `vexa-redis` to `redis:8-alpine` with
`--requirepass ${VEXA_REDIS_PASSWORD}`

**When** `docker compose up -d vexa-redis` runs and the healthcheck stabilises

**Then** `docker exec klai-core-vexa-redis-1 redis-cli -a $VEXA_REDIS_PASSWORD --no-auth-warning INFO server | grep redis_version` reports `redis_version:8.*`
**And** `docker exec klai-core-vexa-redis-1 redis-cli -a $VEXA_REDIS_PASSWORD --no-auth-warning PING` returns `PONG`
**And** `CLIENT INFO` shows the connection as fully authenticated (no ACL warnings)

### N.2 Meeting-api connects and performs pub/sub

**Given** `meeting-api` is up with `REDIS_URL=redis://:${VEXA_REDIS_PASSWORD}@vexa-redis:6379/0`

**When** meeting-api executes its startup Redis handshake

**Then** `docker logs klai-core-meeting-api-1 2>&1 | grep -iE "redis connect|redis error"` shows
successful connect without any `AUTH failed` or `NOAUTH` errors

**And when** an ad-hoc pub/sub test runs:

```bash
# Publisher (inside meeting-api container)
docker exec klai-core-meeting-api-1 python -c "
import asyncio, redis.asyncio as redis, os
async def pub():
    r = redis.from_url(os.environ['REDIS_URL'])
    n = await r.publish('test:vexa003', 'hello')
    print(f'subs={n}')
asyncio.run(pub())
"
# Subscriber (inside runtime-api container, started first in a separate shell)
docker exec klai-core-runtime-api-1 python -c "
import asyncio, redis.asyncio as redis, os
async def sub():
    r = redis.from_url(os.environ['REDIS_URL'])
    p = r.pubsub()
    await p.subscribe('test:vexa003')
    async for msg in p.listen():
        if msg['type']=='message':
            print(msg['data']); break
asyncio.run(sub())
"
```

**Then** the publisher reports `subs=1` (subscriber received the message) and the subscriber
prints `b'hello'` within 5 seconds

### N.3 Streams + consumer group semantics

**Given** Vexa's transcription ingest path uses Redis streams (`XADD` by bot →
`XREADGROUP` by meeting-api collector)

**When** a real meeting runs (§F.1–F.3) OR an ad-hoc stream test is executed:

```bash
docker exec klai-core-vexa-redis-1 redis-cli -a $VEXA_REDIS_PASSWORD --no-auth-warning <<'EOF'
XADD test:stream * key1 val1
XGROUP CREATE test:stream grp1 0 MKSTREAM
XREADGROUP GROUP grp1 consumer1 COUNT 10 STREAMS test:stream >
XACK test:stream grp1 <message-id-from-previous>
XPENDING test:stream grp1
DEL test:stream
EOF
```

**Then** `XADD`, `XGROUP CREATE`, `XREADGROUP`, `XACK`, `XPENDING` all execute without error
**And** real meeting transcription (§F.2) returns transcript segments within 60s — confirming
stream delivery end-to-end in production shape

### N.4 Webhook retry queue round-trip

**Given** the Redis-backed webhook retry queue is keyed as `webhook:retry_queue`
(research.md §3.6)

**When** a webhook delivery is forced to fail (temporarily point `POST_MEETING_HOOKS` at
a non-existent URL, run one meeting to completion, then restore the URL)

**Then** `docker exec klai-core-vexa-redis-1 redis-cli -a $VEXA_REDIS_PASSWORD --no-auth-warning LLEN webhook:retry_queue` shows the failed delivery enqueued (≥ 1)
**And** the `webhook_retry_worker` background task successfully replays it on the next
retry tick once the URL is restored — final state `LLEN = 0`

### N.5 No server-side warnings about deprecated behaviour

**When** `docker logs klai-core-vexa-redis-1 --since 10m 2>&1 | grep -iE "deprecated|warning"` runs

**Then** zero lines match — Redis 8 does not emit deprecation notices for the configuration
and commands Vexa uses

### N.6 Decision revisit trigger

**If** any of N.1–N.5 fails:
- Flip `image: redis:8-alpine` → `image: redis:7.0-alpine` in `deploy/docker-compose.yml`
- Run `docker compose up -d vexa-redis`
- Re-run N.1–N.5 on 7.0-alpine
- Update research.md §5.3 conclusion + add HISTORY row in spec.md
- All other services remain unchanged — no code or compose changes elsewhere

---

## O. Quality gates

### Performance
- Bot container `start_bot` POST to join complete: < 60s P95
- Realtime transcription segment latency (speech → transcript in meeting-api): < 10s P95
- Portal-api `POST /api/bots/start` response time: < 2s P95
- Transcription-service `/health`: < 200ms P95
- Transcription-service 503 fail-fast: < 100ms P95

### Reliability
- All health endpoints pass for 10 consecutive minutes post-deploy
- Webhook retry queue processes at least one simulated failure (kill meeting-api during
  webhook delivery, restart, verify the delivery replays from `webhook:retry_queue` key
  in vexa-redis)
- `gpu-tunnel.service` auto-restart verified (`systemctl stop gpu-tunnel.service`; wait 30s;
  `systemctl status gpu-tunnel.service` shows `active (running)`)

### Security
- All secrets come from SOPS (K above)
- Webhook external calls require Bearer auth (G above)
- `:latest` / `:dev` / `:staging` rejected by pre-commit check (A above)
- Docker socket access limited to runtime-api via `docker-socket-proxy` with
  `CONTAINERS=1 IMAGES=1` only (no `EXEC`, no `SWARM`)

### GDPR
- Transcription runs exclusively on gpu-01 (Hetzner Falkenstein DE)
- `RECORDING_ENABLED=false` by default (no persistent audio)
- No audio or transcript data transferred to any service outside EU (verified via
  `POST_MEETING_HOOKS` pointing only at `portal-api:8010`; `TRANSCRIPTION_SERVICE_URL`
  pointing only at `172.18.0.1:8000` → gpu-01)

---

## P. Definition of Done

All the following must be true:

- [ ] All items in sections A–N passing with evidence attached to a closing note
- [ ] Performance gates (O) met for 10 consecutive minutes of test traffic
- [ ] `git diff --stat` on the klai repo shows the expected files changed:
      `deploy/docker-compose.yml`, `deploy/docker-compose.gpu.yml`, `deploy/vexa/profiles.yaml`,
      `deploy/check-image-tags.sh` (new), `deploy/VERSIONS.md`,
      `klai-portal/backend/app/core/config.py` (minor),
      `klai-scribe/scribe-api/app/services/providers.py`,
      `.moai/specs/SPEC-VEXA-001/spec.md` (status field bumped),
      `.moai/specs/SPEC-VEXA-002/spec.md` (status field bumped)
- [ ] CI green on klai-portal, klai-scribe, klai-infra after merge
- [ ] Server verification: `gh run watch --exit-status` for the deploy workflow → success
- [ ] Browser verification: Playwright MCP run of a full meet-and-transcribe flow shows
      no errors in DevTools console
- [ ] Confidence report attached: `Confidence: [0-100] — [evidence summary]`
- [ ] SPEC-VEXA-001 marked `completed-superseded-by-VEXA-003`
- [ ] SPEC-VEXA-002 marked `cancelled-folded-into-VEXA-003`

---

End of acceptance.md.
