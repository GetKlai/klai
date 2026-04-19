# SPEC-VEXA-003 Deploy Notes — 2026-04-19

Operational details discovered during the Phase 6 live rollout that are not
otherwise captured in `plan.md`. Treat this as append-only: amend, do not
rewrite.

---

## 1. Auth routing through api-gateway (REQUIRED)

**Problem discovered at runtime**: `meeting-api` runs in dev-mode auth when
`API_KEYS` env is unset — it accepts every request but stamps
`user_id = 0`. With `user_id = 0`, `fire_post_meeting_hooks()` silently
skips delivery because the user email cannot be resolved.

**Fix**: route all portal-api → vexa traffic through `api-gateway` (not
directly at `meeting-api`). `api-gateway` validates the `X-API-Key` header
against `api_tokens` in the vexa database, injects trusted identity
headers (`X-User-ID`, `X-User-Scopes`), and proxies to `meeting-api`.

**Implementation (server /opt/klai/.env)**:

```
VEXA_MEETING_API_URL=http://api-gateway:8000
```

Portal-api's `VexaClient` reads `settings.vexa_meeting_api_url` and now hits
the gateway. Network alias `vexa-meeting-api` → `meeting-api` on
`klai-net` is retained for backwards compatibility, but the gateway path
is the canonical one.

**SOPS**: this env var lives in `/opt/klai/.env` directly (plaintext,
mode 600, per Klai pattern). Propagate to klai-infra SOPS repo next time
it is re-rendered.

---

## 2. Vexa DB bootstrap (REQUIRED for webhooks)

On a fresh vexa database, the klai-system user and a matching API token
must exist before portal-api can successfully call `/bots`. Bootstrap SQL:

```sql
-- Klai's service account (user_id = 1)
INSERT INTO users (email, name, max_concurrent_bots, data) VALUES (
  'klai-system@klai.internal',
  'Klai Portal System',
  10,
  '{}'::jsonb
) ON CONFLICT (email) DO UPDATE SET data = EXCLUDED.data;

-- Token that portal-api sends as X-API-Key. Value = $VEXA_API_KEY / $VEXA_BOT_MANAGER_API_KEY.
INSERT INTO api_tokens (token, user_id, scopes, name) VALUES (
  $VEXA_API_KEY,
  (SELECT id FROM users WHERE email = 'klai-system@klai.internal'),
  ARRAY['bot', 'browser', 'tx'],
  'klai-portal-api'
) ON CONFLICT (token) DO UPDATE SET user_id = EXCLUDED.user_id, scopes = EXCLUDED.scopes;
```

**Scopes required** (from `api-gateway/main.py` ROUTE_SCOPES):

| Endpoint prefix | Required scope set (OR) |
|----|----|
| `/bots`      | `bot` or `browser` |
| `/user/`     | `bot`              |
| `/transcripts` | `tx`             |
| `/meetings`  | `tx`               |

A wildcard scope `*` is **not** honoured — the gateway intersects the token
scopes with the route's required set.

**Token cache**: api-gateway caches token validation for 60 s (Redis-backed).
After any scope/user update, restart `klai-core-api-gateway-1` or wait for
TTL.

---

## 3. Per-user webhook is SSRF-blocked for internal targets

`send_completion_webhook()` reads `users.data.webhook_url` and delivers to
it. Before delivery it calls `validate_webhook_url()` which rejects
internal Docker hostnames (`portal-api`, `172.x`, `10.x`, …) to prevent
SSRF. Therefore **do not** set `users.data.webhook_url` to
`http://portal-api:8010/...`.

For internal delivery, use the environment variable
`POST_MEETING_HOOKS=http://portal-api:8010/api/bots/internal/webhook`.
This path runs `fire_post_meeting_hooks()` which bypasses SSRF validation
(internal hooks are explicitly trusted) but still requires
`meeting.start_time` to be set — which only happens when a bot actually
joins a meeting. A fake-URL smoke test will therefore not exercise the
webhook; use a real Google Meet URL.

---

## 4. Stashed server patches — decisions (2026-04-19)

Before the upstream reset, `/opt/klai/vexa-src` carried two working-tree
modifications. They are preserved in git stash `stash@{0}` labelled
`klai-prod-patches-260419` and were assessed:

| Patch hunk | Decision | Rationale |
|------------|----------|-----------|
| `bot-manager/…/process.py` timeouts (waiting 300→60 s, no-one-joined 120→30 s) | DROP | portal-api already overrides per-request via `automatic_leave` body fields (`max_time_left_alone`, `no_one_joined_timeout`, `max_wait_for_admission`). Change portal-api `app/services/vexa.py` if faster behaviour is desired. |
| `bot-manager/…/process.py` RECORDING_ENABLED / captureModes / recordingUploadUrl plumbing | DROP | Upstream main now supports these natively via `RECORDING_ENABLED` + `CAPTURE_MODES` env vars on meeting-api. No patch needed. |
| `bot-manager/…/process.py` bot-stdout → Python logger / docker logs forwarding | DROP (low priority) | Nice observability win, not required for migration. Re-apply later if bot debugging becomes painful. |
| `vexa-bot/…/recording.ts` hybrid `data-participant-id` + text-scan participant counting | DROP | Upstream moved from text-scan to `data-participant-id` + "Leave call" button fallback. Handles screen-share correctly; our text-scan fallback handled DOM edge cases. Functionally equivalent for Klai's common case. Revisit only if a regression is observed in a real Google Meet test. |

Stash stays in place as emergency recovery artefact. Remove with
`git stash drop stash@{0}` only after confidence window.

---

## 5. Image-tag hygiene on server compose

`/opt/klai/docker-compose.yml` was updated in-place (via scp from local,
not via CI sync) because Phase 6 needed to pin image tags immediately.
Vexa image refs on server:

```
vexaai/admin-api:0.10.0-260419-1129
vexaai/api-gateway:0.10.0-260419-1129
vexaai/meeting-api:0.10.0-260419-1129
vexaai/runtime-api:0.10.0-260419-1129
vexaai/vexa-bot:0.10.0-260419-1129   (via BOT_IMAGE_NAME env + BROWSER_IMAGE env)
vexaai/transcription-service:0.10.0-260419-1129   (gpu-01 only)
```

Next `deploy-compose.yml` GitHub Actions run will sync the repo's compose
over. That file already matches (see commit `484fb816`), so CI sync is a
no-op.

---

## 6. Health checks summary (2026-04-19 12:xx)

```
klai-core-admin-api-1                             healthy
klai-core-api-gateway-1                           healthy   (new in this migration; see §G)
klai-core-meeting-api-1                           healthy
klai-core-runtime-api-1                           healthy
klai-core-vexa-redis-1                            healthy
klai-gpu-transcription-api-1   (nginx LB)         healthy
klai-gpu-transcription-worker-1-1                 healthy
klai-gpu-transcription-worker-2-1                 healthy
```

Integration probes from inside portal-api container (clean `200 OK`):

- `http://api-gateway:8000/`                        → 200
- `http://meeting-api:8080/health`                  → 200
- `http://vexa-meeting-api:8080/health` (alias)     → 200
- `http://172.18.0.1:8000/health` (gpu-tunnel)      → 200

Webhook envelope parsing verified by (a) unit execution of deployed
`VexaWebhookPayload.model_validate()` with the SPEC research.md §3.5
fixture and (b) live POST to `/api/bots/internal/webhook` with Bearer
auth → `200 {"status": "ignored"}` and log line
`event: "Vexa webhook: no matching meeting"` showing the envelope was
correctly unpacked.

---

## 7. Pruned images

Freed ~9.8 GiB:

- `vexaai/dashboard:0.10.0-260419-1129`       (286 MB)
- `vexaai/mcp:0.10.0-260419-1129`             (559 MB)
- `vexaai/tts-service:0.10.0-260419-1129`     (502 MB)
- `vexaai/vexa-lite:latest`                   (8.92 GiB)
- `vexa-meeting-api:klai`                     (633 MB)  — pre-migration build
- `vexa-runtime-api:klai`                     (258 MB)  — pre-migration build

Kept as rollback safety (older vexa-bot builds):

- `vexaai/vexa-bot:v20260315-2220` (6.29 GB)
- `vexaai/vexa-bot:latest`         (6.29 GB)

Prune these after confidence window.

---

## 8. Known gaps at end-of-session

1. **No real Google Meet test**. Only synthetic URLs exercised. Real audio
   + transcription path unverified. Required final check before demoing
   to any external person.
2. **Old portal-api-dev / librechat-dev containers** still running
   (`docker ps` shows orphan warnings). Separate cleanup.
3. **ghcr.io/getklai/vexa-lite:latest** (8.98 GiB) still on disk — not sure
   if owned by this project or an unrelated getklai service. Investigate
   before pruning.
4. **api-gateway rate limit** is default 120 rpm. If klai-portal starts
   hitting that, raise via `RATE_LIMIT_RPM` env on api-gateway.

---

*Generated 2026-04-19. Append-only.*
