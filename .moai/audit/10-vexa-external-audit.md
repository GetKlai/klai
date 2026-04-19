# SEC-020 — Vexa external repo audit

**Datum:** 2026-04-19
**Scope:** Vexa services that Klai uses in production: `api-gateway`, `admin-api`, `meeting-api`, `runtime-api`, `vexa-bot`
**Source:** https://github.com/Vexa-ai/vexa (shallow clone, 2026-04-19)

## Context

Original SEC-020 scope was "vexa-bot-manager external auth audit". During the audit it became clear that:
- `vexa-bot-manager` as a separate service does not exist in current Vexa `v0.10.0-260419-1129` release (audit doc `08-vexa.md` finding V-001 confirmed the Caddy `/bots/*` route pointed to a non-existent `vexa-bot-manager:8000` — fixed in SEC-013).
- Current orchestration is split across `api-gateway` (auth + routing), `admin-api` (token issuance), `meeting-api` (scheduling), `runtime-api` (spawns bots), `vexa-bot` (ephemeral Chromium).
- The meaningful audit question is therefore: how does **Klai → Vexa stack** authenticate, and what attack surface does the Vexa stack expose to us?

## Findings

### E-001 — api-gateway auth is fail-closed with good hygiene [POSITIVE]

`services/api-gateway/main.py`:
- `X-API-Key` header scheme via FastAPI `APIKeyHeader`, `auto_error=False` but routes explicitly fail-closed when `require_auth=True` and key is missing or invalid (main.py:298-310).
- Token validation delegated to `admin-api` with Redis cache (60s TTL) — reduces load and fail-closes if admin-api unreachable.
- Rate limiting per token-hash (`hashlib.sha256(api_key)[:16]`) — no prefix collisions, can't be bypassed by varying the key prefix.
- Scope enforcement per route (multi-scope tokens supported).

**Verdict:** solid pattern. No changes needed on Vexa side; Klai can trust the API-key contract.

### E-002 — admin-api uses hmac.compare_digest for admin token [POSITIVE]

`services/admin-api/app/main.py:80`:
```python
if not admin_api_key or not hmac.compare_digest(admin_api_key, ADMIN_API_TOKEN):
    raise HTTPException(status_code=403, detail="Invalid admin API key")
```

Constant-time compare. Matches our own SEC-005/007 patterns. `ADMIN_API_TOKEN` env-sourced, not hardcoded.

**Verdict:** no issue.

### E-003 — Klai sends X-API-Key correctly [POSITIVE]

`klai-portal/backend/app/services/vexa.py:51`:
```python
headers={"X-API-Key": settings.vexa_api_key}
```

Uses the env-sourced `VEXA_API_KEY` (in SOPS). No token leakage in logs confirmed. Paired with `vexa_admin_token` in portal config for admin operations (`SEC-018 DEAD-004` keeps this as `@MX:NOTE` reserved).

**Verdict:** no issue — our integration follows the documented contract.

### E-004 — runtime-api ALLOW_PRIVATE_CALLBACKS=1 is a SSRF window [MEDIUM]

`services/runtime-api/runtime_api/api.py:79-96`:
```python
# Set ALLOW_PRIVATE_CALLBACKS=1 for dev/testing where callbacks target
# internal / private IPs.
...
if config.ALLOW_PRIVATE_CALLBACKS:
    # skip private-IP block
```

`deploy/docker-compose.yml` runtime-api block has `ALLOW_PRIVATE_CALLBACKS: "1"`.

**Risk:** when a meeting is scheduled, `runtime-api` can be given a callback URL for status events. With private callbacks enabled, an attacker who can schedule a meeting could set `callback_url=http://internal-service:port/...` and probe or interact with Klai's internal network via Vexa as SSRF proxy.

**Mitigation status:** partially blocked by api-gateway auth (only authenticated tenants can schedule meetings). Still: any compromised tenant-key is an SSRF privilege escalation.

**Recommendation:**
1. Set `ALLOW_PRIVATE_CALLBACKS=0` in prod compose (and every non-dev env). Already tracked as V-005 in `08-vexa.md`.
2. If callbacks to our own services are required, place those endpoints behind an authenticated path with a specific shared secret — do not rely on IP-range restriction alone.

**Status:** logged in `08-vexa.md` as V-005/F-034 → SEC-013 scope. SEC-013 primary deliverables landed, but this flag was NOT flipped. Follow-up item.

### E-005 — vexa-bot uses Playwright + Stealth Plugin [INFO]

`vexa-bot/core/src/index.ts`:
- Launches `chromium` with `StealthPlugin` (removes `navigator.webdriver`, spoofs plugins)
- `headless: false` — not actually headless; uses Xvfb in the container entrypoint
- `permissions: ["camera", "microphone"]` — expected

**Risk class:** browser-as-attack-surface. A malicious page inside a meeting (shared screen, embedded content) could attempt Chromium RCE. If successful, the bot container has:
- Access to `vexa-bots` network (can pivot to runtime-api, vexa-redis, meeting-api)
- Egress to arbitrary internet (confirmed: `Internal=false` on `vexa-bots` bridge, `docker network inspect` 172.27.0.0/16)
- No secrets mounted (audited: `08-vexa.md` V-008)

**Mitigation already proposed:** SPEC-SEC-022 (egress allowlist for vexa-bots network). That SPEC covers this attack class end-to-end.

### E-006 — Vexa upstream release cadence [INFO]

`admin-api/tests/test_auth.py` + repo commit history shows active development. Upstream `vexa/` repo has no CVE tracker or security.md. Klai pins to specific `vexaai/*:0.10.0-260419-1129` tags (committed in `deploy/docker-compose.yml`), which is correct — `:latest` would be dangerous here.

**Recommendation:** add Vexa image update to quarterly security calendar. Check upstream changelog for security-relevant patches. Owner TBD.

## Summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| E-001 | api-gateway fail-closed + hashed rate limit | POSITIVE | no action |
| E-002 | admin-api constant-time token compare | POSITIVE | no action |
| E-003 | Klai sends X-API-Key correctly from SOPS | POSITIVE | no action |
| E-004 | `ALLOW_PRIVATE_CALLBACKS=1` = SSRF window | MEDIUM | Follow-up ticket: flip flag in compose, retest |
| E-005 | vexa-bot Chromium attack surface | INFO | Covered by SPEC-SEC-022 |
| E-006 | Upstream release cadence needs owner | INFO | Add to ops calendar |

## Consolidated view

The Vexa external codebase is well-structured on the auth path (E-001, E-002). Our integration is correct (E-003). The two meaningful risks are:

1. **Infrastructure-level** (`ALLOW_PRIVATE_CALLBACKS=1`) — single env var, quick flip after testing that our own callbacks don't rely on it.
2. **Bot attack surface** — already addressed by SPEC-SEC-022 (egress allowlist). Not a Vexa problem per se; a Docker-network isolation problem on our side.

No "vexa-bot-manager" surface exists in current production, so the original SEC-020 phrasing is moot. SEC-020 is closed by this audit; remaining actions tracked under SEC-013 (E-004 flag flip) and SPEC-SEC-022 (E-005 egress).
