---
id: SPEC-SEC-AUTH-HARDENING-001
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-CODEBASE-AUDIT-001 (parent, Cluster D)
---

# SPEC-SEC-AUTH-HARDENING-001: Auth-flow hardening (PKCE, replay, SSRF, cookie-isolation, observability)

## Summary

Bundelt 8 HIGH/MED security-findings rond auth-flows: connector-OAuth zonder PKCE, ontbrekende replay-protection op 3 webhooks, research-api SSRF in `convert_url`, gedeelde Fernet-key voor sso+oauth_state cookies, fire-and-forget provision tasks zonder observability.

## Motivation

Per `reports/audit-2026-05-04/auth-flow-review.md` + `renovate-docker-pyramid-adversarial.md` (Adversarial 1, 2, 5):
- **TP-O1 HIGH**: connector-OAuth (Google/MS) zonder PKCE
- **TP-W1 HIGH**: geen replay-protection op Moneybird/Vexa/Gitea webhooks
- **TP-S1 HIGH**: research-api `convert_url` zonder SSRF-guard
- **Adv-1 HIGH**: provision_tenant fire-and-forget zonder observability handoff
- **Adv-2 MED**: OAuth state cookie deelt `sso_cookie_key` (no key-separation)
- **TP-O2 MED**: `klai_oauth_state` op brede `.getklai.com` domain (niet `__Host-`)
- **TP-W3 MED**: geen rate-limit op portal/ingest webhooks
- **Adv-5 MED**: deprovision lock-released-before-task-queued race

## Scope

### In scope

1. **PKCE S256** op `/api/oauth/{provider}/authorize` voor Google/MS connector OAuth + state-payload uitbreiden met `code_verifier` (32 bytes urlsafe)
2. **`klai-libs/webhook-replay`** package extracten uit mailer's `app/nonce.py` + adopteren in portal Moneybird/Vexa webhooks + ingest Gitea webhook
3. **`research-api/services/docling.py::convert_url`** wrap in `validate_url_pinned()` import uit `klai_image_storage.url_guard`
4. **`oauth_state_cookie_key`** Settings-veld + Fernet-instantiatie + HKDF-derive uit `portal_secrets_key` (nieuwe key-class)
5. **`__Host-klai_oauth_state`** cookie-prefix (geen domain-attribute)
6. **Provision/deprovision observability**: 202 Accepted + polling URL OF Redis Streams queue ipv `BackgroundTasks.add_task`
7. **Deprovision row-lock fix**: audit-emit synchroon vóór 202 return; long-running steps na queueing
8. **Per-source rate-limit** op portal/ingest webhooks via mailer's sliding-window pattern

### Out of scope

- Settings-validators (gedekt door SPEC-SEC-VALIDATOR-COVERAGE-001)
- klai-docs CSP/rehype-sanitize (al PR #313)
- ingest header drift (al PR #314)

## Acceptance criteria

1. Per fix: dedicated test in respectievelijke service `tests/`
2. PKCE: state-payload contains `code_verifier`; OAuth callback verifies via `code_verifier` matches `code_challenge`; test van replay en wrong-verifier
3. webhook-replay: nonce-store TTL 5min; fail-closed bij Redis-down (per mailer pattern); test cross-service consume
4. research-api SSRF: `validate_url_pinned` faalt op `http://portal-api:8010/...` met test-fixture
5. oauth_state_cookie_key: rotation kan zonder impact op sso_cookie_key
6. Provision observability: status-endpoint dat tenant-state polling toelaat
7. Geen 401-cascade tijdens rollout (atomic deploy per fix)

## Sequencing (per fix in eigen PR)

1. webhook-replay extract (laaghangend, geen breaking change)
2. research-api SSRF fix (single-file, single-test)
3. PKCE OAuth (single-service portal-api)
4. oauth_state_cookie_key separation (vereist SOPS env-var pre-flight)
5. `__Host-` cookie prefix (klein, snel)
6. Provision/deprovision observability (groot, eigen sub-SPEC mogelijk)
7. Webhook rate-limit (afgeleid van mailer pattern)

## References

- `reports/audit-2026-05-04/auth-flow-review.md`
- `reports/audit-2026-05-04/renovate-docker-pyramid-adversarial.md` (sectie 3.12 Adversarial)
- `klai-mailer/app/nonce.py` — replay-protection canonical pattern
- `klai-libs/image-storage/url_guard.py::validate_url_pinned`
- RFC 9700 OAuth 2.0 Security BCP
