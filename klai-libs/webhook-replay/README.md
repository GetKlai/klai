# klai-webhook-replay

Shared Redis-backed webhook replay-protection nonce store for Klai services.

Extracted from `klai-mailer/app/nonce.py` as part of SPEC-SEC-AUTH-HARDENING-001 item 1.
The canonical reference implementation is `webhook_replay.nonce_store.WebhookNonceStore`.

## Usage

```python
from webhook_replay import WebhookNonceStore, NonceReplayError, RedisUnavailableError

store = WebhookNonceStore(
    redis_url="redis://redis:6379/0",
    prefix="myservice:nonce:",
    ttl_seconds=300,
)

# In your webhook handler (after HMAC verification):
try:
    await store.check_and_record(timestamp, signature_hash)
except NonceReplayError:
    raise HTTPException(401, detail="invalid signature")
except RedisUnavailableError:
    raise HTTPException(503, detail="Service unavailable")
```

## Services

- **klai-mailer** (PR A, canonical reference) — Zitadel webhook, prefix `mailer:nonce:`
- **portal-api** (PR B, planned) — Moneybird + Vexa webhooks
- **knowledge-ingest** (PR C, planned) — Gitea webhooks
