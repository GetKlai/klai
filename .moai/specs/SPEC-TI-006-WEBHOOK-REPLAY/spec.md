# SPEC-TI-006 — Webhook replay-protection adoption

**Audit ref:** findings **C-9** (Moneybird/Vexa) + **C-10** (Vexa secret tightening)
**Standards ref:** `standards.md` sections 6, 15
**Priority:** HIGH
**Status:** Ready

## Goal

Adopt `klai-libs/webhook-replay` (extracted in PR #335) op Moneybird, Vexa, en Gitea webhooks. Eliminate replay-primitive op live billing/meeting/ingest events.

## Acceptance criteria (EARS)

### Moneybird (C-9 deel 1)
- **AC-1** `klai-portal/backend/app/api/webhooks.py` Moneybird handler: replay-check via `WebhookNonceStore(prefix="portal:moneybird-nonce:", ttl=300)`, parts `(event_id, timestamp)`, NA HMAC-verificatie maar VOOR side-effects.
- **AC-2** `NonceReplayError` → HTTP 409 `replay_blocked`.
- **AC-3** `RedisUnavailableError` → HTTP 503 (fail-closed).

### Vexa (C-9 deel 2 + C-10)
- **AC-4** `klai-portal/backend/app/api/meetings.py` Vexa handler: replay-check, parts `(vexa_meeting_id, status, timestamp)`.
- **AC-5** Vexa-handler ACCEPT alleen events voor meetings met `status in ACTIVE_STATUSES` op de `vexa_meeting_id` branch (vandaag alleen op platform+native_meeting_id branch).

### Gitea (C-9 deel 3)
- **AC-6** `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py` Gitea handler: replay-check, parts `(delivery_id,)` uit `X-Gitea-Delivery` header.

## Implementation

1. `klai-portal/backend/pyproject.toml`: add `klai-webhook-replay = { path = "../../klai-libs/webhook-replay" }` als path-dep.
2. `klai-knowledge-ingest/pyproject.toml`: idem.
3. Module-scope `WebhookNonceStore` instance per webhook (drie totaal).
4. Auth-volgorde per `standards.md` 15: HMAC verify → replay check → tenant resolution → side-effects.

## Tests

- `test_moneybird_webhook_replay.py`: replay → 409, fresh → 200.
- `test_vexa_webhook_replay.py`: idem + active-status filter test.
- `test_gitea_webhook_replay.py`: idem.
- Mock Redis via fakeredis (al gebruikt in mailer tests).

## Operator-step (post-deploy)

Geen migratie. Verifieer Redis bereikbaarheid:
```bash
docker exec klai-core-portal-api-1 python -c "import asyncio; from app.core.redis import get_redis; print(asyncio.run(get_redis().ping()))"
```

## Worktree

`klai-webhook-replay` — `feature/SPEC-TI-006-WEBHOOK-REPLAY`.
