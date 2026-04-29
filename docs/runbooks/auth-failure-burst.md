# Runbook: portal-api auth endpoint failure burst

**SPEC**: SPEC-SEC-AUTH-COVERAGE-001
**Alert sources**: `deploy/grafana/provisioning/alerting/portal-auth-rules.yaml`
**Owner rotation**: security on-call (R2 critical) / ops on-call (R1 warning)

This runbook covers two alerts that fire on the structured `*_failed` events
emitted by `_emit_auth_event` in `klai-portal/backend/app/api/auth.py`:

| Alert | Severity | Window | Threshold |
|---|---|---|---|
| `auth_failure_rate_high` (R1) | warning  | 5m | > 10 events |
| `auth_zitadel_5xx_burst` (R2)  | critical | 1m | > 5  events with `reason=zitadel_5xx` |

For the dedicated MFA enforcement alert (`mfa_check_failed_*`), see
[mfa-check-failed.md](mfa-check-failed.md). MFA events are intentionally
excluded from the auth-coverage rules to avoid double-paging.

## Event taxonomy

The 16 events covered by R1, grouped by the auth flow they instrument:

| Flow | Events |
|---|---|
| TOTP MFA | `totp_setup_failed`, `totp_confirm_failed`, `totp_login_failed` |
| Passkey MFA | `passkey_setup_failed`, `passkey_confirm_failed` |
| Email-OTP MFA | `email_otp_setup_failed`, `email_otp_confirm_failed`, `email_otp_resend_failed` |
| IDP login | `idp_intent_failed`, `idp_callback_failed` |
| IDP signup | `idp_intent_signup_failed`, `idp_signup_callback_failed` |
| Password reset | `password_reset_failed`, `password_set_failed` |
| SSO completion | `sso_complete_failed` |
| Email verification | `verify_email_failed` |

Every event carries the same field shape (per `_emit_auth_event`):

| Field | Values |
|---|---|
| `event` | one of the 16 above |
| `reason` | `zitadel_5xx` / `invalid_code` / `expired_link` / `unknown_idp` / `find_user_by_email_5xx` / `db_lookup_failed` / `unexpected` / etc. |
| `outcome` | HTTP status code as string (`"400"`, `"401"`, `"502"`, `"302"`) |
| `zitadel_status` | upstream HTTP status when applicable |
| `email_hash` | sha256 hex of email (privacy-safe) |
| `level` | `warning` / `error` |

## Triage R1 (auth_failure_rate_high)

This is a broad burst alert. Goal: identify whether one endpoint regressed
or many endpoints are failing simultaneously.

### Step 1 — group by event + reason

Run in Grafana → Explore (VictoriaLogs datasource):

```
_time:5m service:portal-api event:in(
  totp_setup_failed,totp_confirm_failed,totp_login_failed,
  passkey_setup_failed,passkey_confirm_failed,
  email_otp_setup_failed,email_otp_confirm_failed,email_otp_resend_failed,
  idp_intent_failed,idp_intent_signup_failed,
  idp_callback_failed,idp_signup_callback_failed,
  password_reset_failed,password_set_failed,
  sso_complete_failed,verify_email_failed
) | stats by (event, reason, outcome) count()
```

### Step 2 — interpret

- **Multiple events, dominated by `reason=zitadel_5xx`** → R2 should be
  firing too. Triage [R2](#triage-zitadel-5xx-burst) first; the
  underlying cause is shared.
- **Single event dominates, `reason=zitadel_5xx`** → Zitadel endpoint that
  this flow uses is degraded. Check whether other portal-api Zitadel
  callers are healthy:
  ```
  _time:5m service:portal-api zitadel_status:5* | stats by (event) count()
  ```
- **Single event dominates, `reason ≠ zitadel_5xx`** → likely a code
  regression on that specific endpoint. Check recent merges to
  `klai-portal/backend/app/api/auth.py` (focus on the function for the
  failing event). Common patterns:
  - `reason=invalid_code` spikes on `*_confirm_failed` / `*_login_failed`
    → could be a brute-force probe; check by `email_hash` distribution.
  - `reason=expired_link` spikes on `password_set_failed` /
    `verify_email_failed` → email delivery latency increased; check
    klai-mailer health.
  - `reason=unexpected` on any event → check container error logs:
    `_time:30m service:portal-api level:error`.

### Step 3 — confirm user impact

`outcome` values map to HTTP responses returned to users:

- `400` / `401` / `404` / `409` → client-side errors. Often expected
  (wrong code, expired link). Burst = unusual usage pattern, not outage.
- `502` / `503` → server-side failures. Each event = a real user blocked.
  Open a status update if sustained.
- `302` → redirect to a signed `failure_url` (idp_callback /
  idp_signup_callback only). User saw an OAuth error page. Less visible
  than 5xx but still real.

### Step 4 — if no clear pattern

Brute-force probe check — many `email_hash` values for `*_confirm_failed`
or `*_login_failed`:

```
_time:5m service:portal-api event:in(totp_login_failed,email_otp_confirm_failed,passkey_confirm_failed,totp_confirm_failed)
  | stats by (email_hash) count()
  | where count() > 3
```

If many distinct `email_hash` values appear with > 3 failures each in
5 minutes, this looks like enumeration. Escalate to security on-call.

## Triage Zitadel 5xx burst

(R2 `auth_zitadel_5xx_burst`)

Critical alert. Cross-endpoint spike of `reason=zitadel_5xx` indicates
Zitadel itself is unhealthy — a wide blast radius affecting login,
signup, MFA, and account-flow endpoints simultaneously.

### Step 1 — confirm Zitadel as root cause

```
curl -fsS https://auth.getklai.com/debug/healthz
```

Check Zitadel error logs:

```
_time:5m service:zitadel level:error | stats by (msg) count()
```

If Zitadel is genuinely 5xx-ing → confirmed root cause; skip to step 4.

### Step 2 — confirm cross-endpoint spread

```
_time:5m service:portal-api reason:zitadel_5xx | stats by (event) count()
```

If only one event has all the failures → it's an endpoint regression
masquerading as Zitadel. Triage as [R1 step 2](#step-2--interpret).

If 3+ distinct events show non-zero counts → cross-endpoint spread
confirmed.

### Step 3 — rule out portal-api → Zitadel network path

If Zitadel is healthy but portal-api still sees 5xx:

```bash
# From core-01:
docker exec klai-core-portal-api-1 python -c "import httpx; print(httpx.get('https://auth.getklai.com/debug/healthz').status_code)"
```

If the container cannot reach Zitadel:

- DNS in container: `docker exec klai-core-portal-api-1 getent hosts auth.getklai.com`
- httpx client connection pool exhaustion: check `_time:30m service:portal-api "ConnectError"`
- TLS chain issue: `docker exec klai-core-portal-api-1 openssl s_client -connect auth.getklai.com:443 -servername auth.getklai.com < /dev/null`

### Step 4 — Zitadel outage handling

When Zitadel is down, do NOT change auth.py code:

- Login / TOTP login / email-OTP confirm → return 502; users cannot log in.
- IDP callback / signup callback → return 502 OR 302 to failure_url.
- Password reset / verify_email → return 502.
- TOTP setup / passkey setup / email-otp setup → return 502 (config flows).

Wait for Zitadel recovery. Post a status update if outage > 5 minutes.

DO NOT temporarily disable MFA enforcement, dev-mode switch, or any
fail-open code path during a Zitadel outage. Doing so degrades from
"unavailable" to "insecure". The system is correctly fail-closed; user
inconvenience is acceptable for the duration of the outage.

### Step 5 — escalation

If Zitadel is healthy AND R2 persists for > 5 minutes AND R1 step 4
brute-force check is also positive:

- Page security on-call.
- Consider Caddy-level rate-limit on `/api/auth/*` if attack volume is
  high.
- Capture `email_hash` distribution for incident report.
