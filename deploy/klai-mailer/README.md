# klai-mailer

Lightweight transactional email service for Klai. Receives webhook notifications from Zitadel and sends Klai-branded HTML emails via SMTP.

## Architecture

```
Zitadel → POST /notify (signed) → klai-mailer → SMTP → recipient
```

klai-mailer acts as a [Zitadel HTTP notification provider](https://zitadel.com/docs/self-hosting/manage/email). Zitadel pre-renders message texts (subject, greeting, body, button, footer) and sends them as a signed webhook. klai-mailer wraps the content in the Klai HTML template and dispatches via SMTP.

**What this replaces:** Zitadel's built-in SMTP sending, which only supports plain-text or basic HTML with no custom branding.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness check — returns `{"status": "ok"}` |
| `POST` | `/notify` | Zitadel webhook — authenticated via HMAC-SHA256 signature |
| `POST` | `/debug` | Log raw payload — only available when `DEBUG=true` |

## Environment variables

Copy `.env.example` to `.env` (or decrypt `.env.sops` on the server).

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | required | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (587 = STARTTLS, 465 = implicit TLS) |
| `SMTP_USERNAME` | required | SMTP auth username |
| `SMTP_PASSWORD` | required | SMTP auth password |
| `SMTP_FROM` | `noreply@${DOMAIN}` | Sender email address |
| `SMTP_FROM_NAME` | `Klai` | Sender display name |
| `SMTP_TLS` | `true` | Use STARTTLS (set `false` when using port 465) |
| `SMTP_SSL` | `false` | Use implicit TLS (set `true` for port 465) |
| `WEBHOOK_SECRET` | required | Shared secret for HMAC-SHA256 signature verification |
| `LOGO_URL` | `https://www.${DOMAIN}/klai-logo.png` | Email header logo |
| `LOGO_WIDTH` | `61` | Logo width in pixels |
| `BRAND_URL` | `https://www.${DOMAIN}` | Brand link in email footer |
| `DEBUG` | `false` | Enable `/debug` endpoint — never enable in production |

Generate a webhook secret:
```bash
openssl rand -hex 32
```

## Notification types

Five event types are handled (configured in Zitadel console under Instance > Settings > Message Texts):

| Type | Trigger |
|------|---------|
| `InitCode` | Account created — activation email |
| `VerifyEmail` | Email address verification |
| `PasswordReset` | Password reset request |
| `PasswordChange` | Password changed notification |
| `InviteUser` | Admin invites a user to the organisation |

Message text templates (subject, greeting, body, button text, footer) live in `zitadel-message-texts/en.yaml` and `nl.yaml`. These are applied in the Zitadel console; klai-mailer receives the already-rendered content.

## Deployment

The service runs as part of the klai core stack:

```bash
# On core-01, from /opt/klai/
docker compose up -d klai-mailer

# View logs
docker compose logs -f klai-mailer

# Health check
curl http://localhost:8001/health
```

Port mapping: `127.0.0.1:8001:8000` — localhost-only, not externally reachable.

Docker network: `klai-net` (shared with Zitadel so Zitadel can reach `http://klai-mailer:8000/notify`).

## Zitadel configuration

In the Zitadel console under **Instance > Settings > Notifications**:

- **Notification provider type:** HTTP
- **Endpoint:** `http://klai-mailer:8000/notify`
- **Signing secret:** value of `WEBHOOK_SECRET`

Zitadel signs every request with `ZITADEL-Signature: t={timestamp},v1={hmac_sha256}`. klai-mailer validates the signature and rejects requests older than 5 minutes.

## Signature verification

The `ZITADEL-Signature` header format: `t={unix_timestamp},v1={hmac_hex}`

Verification steps:
1. Extract timestamp and HMAC from header
2. Reject if timestamp is older than 5 minutes (replay attack protection)
3. Compute `HMAC-SHA256(webhook_secret, "{timestamp}.{raw_body}")`
4. Compare with `v1` using constant-time comparison

Returns `401` on verification failure (Zitadel will not retry). Returns `5xx` on SMTP failure (Zitadel retries).

## Email template

The HTML wrapper is in `theme/email.html.j2`. It is a table-based responsive template with inline CSS for Outlook/Gmail/Apple Mail compatibility. Klai colour scheme:

- Background: `#F5F0E8` (sand light)
- Card: `#ffffff`
- Primary button: `#7c6aff` (purple)
- Text: `#1a0f40`

To update the template, edit `theme/email.html.j2` and rebuild the container:
```bash
docker compose build klai-mailer
docker compose up -d klai-mailer
```

## Debugging

Enable the debug endpoint temporarily (never in production):

```env
DEBUG=true
```

Then send a test webhook:
```bash
# Generate signature (replace SECRET and BODY with real values)
TIMESTAMP=$(date +%s)
BODY='{"your":"payload"}'
SIG=$(echo -n "${TIMESTAMP}.${BODY}" | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $2}')
curl -X POST http://localhost:8001/debug \
  -H "ZITADEL-Signature: t=${TIMESTAMP},v1=${SIG}" \
  -H "Content-Type: application/json" \
  -d "$BODY"
```

The raw payload is logged to stdout. View with `docker compose logs klai-mailer`.

## Development

```bash
cd deploy/klai-mailer
pip install -e .
cp .env.example .env  # fill in values
uvicorn app.main:app --reload --port 8000
```

## Dependencies

- `fastapi` + `uvicorn` — HTTP server
- `jinja2` — HTML template rendering
- `aiosmtplib` — Async SMTP client
- `pydantic` + `pydantic-settings` — Data validation and config

No database. No external API calls. Pure render + send.
