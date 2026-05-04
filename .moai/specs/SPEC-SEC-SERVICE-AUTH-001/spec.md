---
id: SPEC-SEC-SERVICE-AUTH-001
version: "1.0"
status: draft
created: 2026-05-01
updated: 2026-05-01
author: Mark Vletter
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-01 | Mark Vletter | Initial draft. Triggered by 2026-05-01 incident: LiteLLM → retrieval-api `X-Internal-Secret` drift causing silent KB-augmentation failure on Voys tenant. Replaces shared-secret pattern with Zitadel-issued OAuth 2.0 Client Credentials JWTs, per OWASP 2025 microservice security cheat-sheet update + RFC 6749 §4.4. |

---

# SPEC-SEC-SERVICE-AUTH-001: Replace shared `X-Internal-Secret` with Zitadel-issued service JWTs

## Context

Klai's internal services (portal-api, knowledge-ingest, klai-connector, klai-mailer, retrieval-api, klai-knowledge-mcp, scribe-api, research-api, LiteLLM hook) authenticate to each other today via a shared bearer secret. Each receiver checks `X-Internal-Secret` against an env var. Multiple env-var names exist in parallel (`INTERNAL_SECRET`, `PORTAL_INTERNAL_SECRET`, `KNOWLEDGE_INGEST_SECRET`, `KLAI_CONNECTOR_SECRET`) and historically drift, causing silent failures.

### Concrete incident (2026-05-01)

LiteLLM's `KlaiKnowledgeHook` calls `POST http://retrieval-api:8040/retrieve` with header `X-Internal-Secret: <PORTAL_INTERNAL_SECRET>`. retrieval-api validates this header against its own `INTERNAL_SECRET` env var. The values diverged at some point in production, causing every chat on `chat-voys.getklai.com` to hit `401 Unauthorized` at the retrieval-api boundary. The hook is fail-open — chats kept working but without KB augmentation. Zero `knowledge.queried` events emitted across the Voys tenant for hours before discovery.

Root cause is structural, not a one-off configuration mistake:

| Pattern weakness | Concrete impact in klai |
|---|---|
| Long-lived shared secret across N services | One leak compromises ALL inter-service auth simultaneously |
| Receiver knows "valid secret arrived", not WHO called | Logs show `auth.method="internal", role="service"` — no caller identity for audit |
| No granular authorization | A hypothetical compromised LiteLLM secret can call any `X-Internal-Secret`-protected endpoint, including `connector-purge` and `finalize-delete` |
| Multiple env-var names refer to the same canonical secret | Drift is invisible until a request fails 401 |
| Rotation requires N coordinated deploys | Has produced ≥3 incident-class auth bugs in the past 6 months |

### Industry standard (2025-2026)

OWASP Microservices Security Cheat Sheet (updated August 2025) recognises two recommended patterns for service-to-service auth:

1. **mTLS** with workload identities (SPIFFE/SPIRE). Gold standard for service-mesh deployments.
2. **Internal tokens** issued by an authorization server (OAuth 2.0 Client Credentials grant, RFC 6749 §4.4).

Shared secrets are *not* in the recommended list. Quoted from innoq's 2025 update of the OWASP guidance: *"current guidance prefers short-lived credentials over long-lived secrets embedded in config"*. RFC 6749 lists the security advantages of tokens over static secrets: temporal limitation, scope restriction, auditability, revocation agility, reduced blast radius.

### Why OAuth 2.0 Client Credentials fits klai

* Zitadel is the existing identity provider. retrieval-api and klai-connector already validate Zitadel-issued JWTs via `_decode_jwt` middleware paths — only the *sending* side is missing.
* Service mesh (Istio/Linkerd/Consul Connect) is not deployed and would be disproportionate infrastructure for klai's current docker-compose runtime.
* Per-service identity becomes available: caller `sub` claim differentiates `svc-litellm` from `svc-portal-api` from `svc-research-api`.
* Per-endpoint authorization becomes available via scope claim: `klai:internal:retrieval:query` distinct from `klai:internal:purge`.
* Token TTL ≤ 1h limits leak blast radius to the same window.

---

## Scope

In scope (this SPEC):

- New shared library `klai-libs/service-auth` providing a `ZitadelTokenClient` for outbound services to mint short-lived JWTs.
- Zitadel service account provisioning script for one machine user per service.
- New auth path on receiver side: scope-aware JWT validation alongside the legacy `X-Internal-Secret` middleware.
- First migrated pair: LiteLLM `KlaiKnowledgeHook` → `retrieval-api` `/retrieve`. This pair both proves the pattern and resolves the 2026-05-01 incident.
- Migration runbook + observability (Grafana panel for auth-method distribution).

Out of scope (explicit non-goals for this SPEC; tracked in follow-ups):

- Migrating remaining service pairs (portal-api ↔ knowledge-ingest, portal-api ↔ klai-connector, etc.). Each pair is its own incremental PR using the same library + pattern. Captured in §"Migration Phases" below as Phase C2-Cn but executed in separate PRs.
- Removing the legacy `X-Internal-Secret` middleware paths. Phase D, gated on all pairs migrated.
- Migrating to mTLS / service mesh.
- Replacing user JWT auth (already Zitadel-based).
- Rotating secrets that survive the migration (knowledge-ingest still has webhook secrets for Gitea, Moneybird etc. — those are separate inbound surfaces).

---

## Requirements (EARS)

### REQ-1 (Service identity)

**WHEN** a klai service starts, **THE SYSTEM SHALL** authenticate to other internal klai services using a Zitadel-issued JWT minted via the OAuth 2.0 Client Credentials grant (RFC 6749 §4.4) against `https://auth.getklai.com/oauth/v2/token`.

Each service has exactly one Zitadel machine user (service account). Naming convention: `svc-{servicename}` in the klai platform organisation.

### REQ-2 (Token client library)

A shared library `klai-libs/service-auth` SHALL provide `ZitadelTokenClient` with:

- Constructor: `(client_id, client_secret, token_url, scope=None)`.
- `async get_token() -> str` returns a valid JWT, minting on first call, caching with proactive refresh at 80% of advertised TTL.
- Concurrent calls under a single `asyncio.Lock` to prevent thundering-herd token mints during cache-miss.
- Configurable timeout (default 10s) on the token endpoint.
- Structured logging: `service_auth_token_minted`, `service_auth_token_mint_failed`, `service_auth_token_cache_hit`.
- Fail-fast on missing/empty `client_id` or `client_secret` at construction time (no silent fallback to legacy auth).

### REQ-3 (Receiver scope enforcement)

On the receiver side, every `X-Internal-Secret`-protected endpoint SHALL gain a parallel JWT-authenticated path with explicit scope check. A `@require_scope("klai:internal:<scope-name>")` decorator (or equivalent middleware) SHALL:

- Extract caller `sub` claim and required scope from the JWT.
- Reject (403 `insufficient_scope`) when the JWT's `scope` claim does not contain the required scope.
- Log caller identity (`sub`) on success for audit traceability.

For the LiteLLM → retrieval-api pair specifically: `/retrieve` requires scope `klai:internal:retrieval:query`. `svc-litellm` receives that scope; no other service does.

### REQ-4 (Dual auth during migration)

Receivers SHALL accept BOTH the legacy `X-Internal-Secret` header AND the new `Authorization: Bearer <jwt>` header during migration. Precedence: JWT first; fall back to internal secret only if no Authorization header is present. This MUST hold until Phase D removes the legacy path.

Each accepted call SHALL emit a structured log field `auth.path=jwt|internal_secret` so a Grafana panel can track migration progress.

### REQ-5 (Caller dual auth + safe rollout)

The first migrated caller (LiteLLM `KlaiKnowledgeHook`) SHALL try JWT auth first, and on JWT mint failure (Zitadel down, secret invalid, network error) SHALL log a warning and fall back to legacy `X-Internal-Secret`. Fallback behaviour MUST be deleted in Phase C-cleanup once production traffic has been on the JWT path for ≥7 days with zero `service_auth_token_mint_failed` events.

This safe-rollout is scoped to the first pair only. Subsequent pairs in Phase C2..Cn use the proven library and DO NOT require the dual-caller fallback.

### REQ-6 (Bootstrap idempotency)

The Zitadel service-account bootstrap script (`klai-infra/scripts/zitadel-create-service-account.py`) SHALL be idempotent: running it twice with the same `--name` arg either reuses the existing machine user (logging "service_account_exists") or creates a new one — never duplicates.

The script SHALL NOT generate or print client_secret values to stdout in plaintext. It SHALL write the secret to a temp file with mode 0600 and instruct the operator to encrypt it via SOPS into `klai-infra/core-01/.env.sops`.

### REQ-7 (Validator parity)

Per `.claude/rules/klai/pitfalls/process-rules.md::validator-env-parity`: the env vars consumed by `ZitadelTokenClient` (`KLAI_*_CLIENT_ID`, `KLAI_*_CLIENT_SECRET`, `KLAI_OAUTH_TOKEN_URL`) MUST be present in `klai-infra/core-01/.env.sops` and the relevant `deploy/docker-compose.yml` `environment:` block BEFORE the code that reads them is deployed. The deploy order for the first pair MUST be:

1. SOPS env vars committed and synced to core-01.
2. retrieval-api scope-enforcement code deployed.
3. LiteLLM hook JWT-auth code deployed.

Deploying in any other order causes a fail-closed startup loop or breaks the chat path.

### REQ-8 (Observability)

A Grafana panel "Service auth method distribution" SHALL plot the rate of `auth.path=jwt` vs `auth.path=internal_secret` events across all receivers, sliced by service. Migration is considered complete when 100% of inter-service traffic shows `auth.path=jwt` for ≥7 days.

A second Grafana panel "Service auth failures" SHALL alert on:
- ≥1 `service_auth_token_mint_failed` event in 5 minutes (Zitadel availability).
- ≥1 `auth_rejected` with `reason=invalid_jwt_signature` from a known service `sub` (config drift).

---

## Migration Phases

The phased migration MUST be incremental — each phase is independently deployable + testable + rollback-able.

### Phase A — Foundation (this SPEC)

1. New library `klai-libs/service-auth` with `ZitadelTokenClient` + tests.
2. Zitadel bootstrap script `klai-infra/scripts/zitadel-create-service-account.py`.
3. Operator-run: create `svc-litellm` machine user via the script.
4. Operator-run: encrypt `KLAI_LITELLM_CLIENT_ID` + `KLAI_LITELLM_CLIENT_SECRET` + `KLAI_OAUTH_TOKEN_URL` into `klai-infra/core-01/.env.sops`. Push to klai-infra and verify SOPS sync workflow propagated to core-01.

### Phase B — Receiver readiness (this SPEC)

5. retrieval-api: add `@require_scope` decorator + scope check on `/retrieve` (alongside legacy). Deploy. Verify legacy path still works.
6. retrieval-api: add structured `auth.path` log field per request.

### Phase C-1 — First caller migration (this SPEC)

7. LiteLLM hook: load `ZitadelTokenClient` at module init. Try JWT auth first on `/retrieve`. Fall back to `X-Internal-Secret` on JWT mint failure (REQ-5).
8. Deploy LiteLLM. Trigger a Voys chat. Verify `knowledge.queried` event emits and retrieval-api logs `auth.path=jwt, sub=svc-litellm`.
9. 7-day soak. Monitor Grafana panels for any fallback events.
10. Remove the legacy fallback in the LiteLLM hook code (delete the `except → X-Internal-Secret` branch).

### Phase C-2..Cn — Remaining pairs (separate SPECs)

For each remaining caller-receiver pair:

| Pair | Caller | Receiver | Scope |
|---|---|---|---|
| C-2 | portal-api | knowledge-ingest | `klai:internal:ingest:write` |
| C-3 | portal-api | klai-connector | `klai:internal:connector:invoke` |
| C-4 | knowledge-ingest | portal-api (`/internal/connectors/.../finalize-delete`) | `klai:internal:portal:callback` |
| C-5 | klai-connector | portal-api callbacks | `klai:internal:portal:callback` |
| C-6 | klai-connector | knowledge-ingest (crawl_sync) | `klai:internal:ingest:crawl` |
| C-7 | klai-knowledge-mcp | knowledge-ingest, retrieval-api | `klai:internal:ingest:read`, `klai:internal:retrieval:query` |
| C-8 | research-api / klai-focus | retrieval-api | `klai:internal:retrieval:query` |
| C-9 | scribe-api | portal-api callbacks | `klai:internal:portal:callback` |
| C-10 | mailer | portal-api callbacks | `klai:internal:portal:callback` |

Each pair SHALL follow the same template: receiver-side scope decorator + caller-side library wiring + 7-day soak + legacy removal.

### Phase D — Legacy removal (separate SPEC)

11. Verify Grafana panel shows 100% `auth.path=jwt` across all receivers for ≥7 days.
12. Remove `X-Internal-Secret` validation paths from each receiver's middleware.
13. Remove `INTERNAL_SECRET`, `PORTAL_INTERNAL_SECRET`, `KNOWLEDGE_INGEST_SECRET`, `KLAI_CONNECTOR_SECRET` env vars from SOPS + docker-compose.yml.
14. Update `.claude/rules/klai/pitfalls/process-rules.md` to reflect the new pattern.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Zitadel goes down → all internal traffic fails | Token client caches for 80% of TTL (≈48 min). Single Zitadel outage <1h is invisible. Outages >1h are an incident regardless. |
| Service-account secret leaks via env var | Same blast-radius window as a token (≤ 1h after rotation). Rotation script in `klai-infra/scripts/rotate-service-account.py` (Phase D). |
| First-caller broken → chat without KB | REQ-5 fallback to legacy keeps chat KB-augmented during the 7-day soak. After soak removal, KB augmentation depends on JWT path; same dependency exists for legacy today. |
| Validator-parity bug repeats (the SPEC-SEC-WEBHOOK-001 incident pattern) | REQ-7 mandates SOPS-first deploy ordering. PR-checklist enforced. |
| Per-service scope strings drift like env-var names did | Scope strings live in `klai-libs/service-auth/scopes.py` as constants — identical pattern to `knowledge_ingest/queues.py`. Tests pin no-drift. |
| LiteLLM hook is a custom callback in upstream LiteLLM project, hot-reloaded | Hook lives at `/opt/klai/litellm/klai_knowledge.py` (klai-side). Token-client init at module load + DI works because LiteLLM persists imports. Tested via `tests/test_klai_knowledge_templates.py` pattern. |

---

## Success Criteria

1. After Phase B deploy: retrieval-api logs `auth.path=internal_secret` for all `/retrieve` requests (legacy still working). New JWT path code deployed but inactive.
2. After Phase C-1 deploy: ≥99% of `/retrieve` requests log `auth.path=jwt, sub=svc-litellm`. 0 calls to legacy fallback under normal conditions.
3. Voys chat at `chat-voys.getklai.com` produces `knowledge.queried` events again. The 2026-05-01 incident is resolved.
4. Unit tests passing:
   - `klai-libs/service-auth/tests/` ≥ 10 tests covering token mint, cache hit, cache expiry, lock contention, mint failure, scope passing, fail-fast on missing config.
   - `klai-retrieval-api/tests/` new tests for scope decorator: pass with right scope, reject 403 with wrong scope, reject 403 with no scope, accept legacy internal_secret path.
5. Grafana panel "Service auth method distribution" deployed and populated.

---

## Out of scope (explicit non-goals for this SPEC)

- mTLS / service mesh adoption. Captured as a future architecture goal; not blocking.
- Replacing Zitadel as IdP.
- Migrating user-auth flows (already Zitadel-based, working).
- Inbound webhook auth for Gitea / Moneybird / Vexa — those are external callers and use webhook-specific HMAC patterns.
- Token introspection vs JWT validation trade-off study. Default: validate JWT signature against Zitadel JWKS locally (no per-call introspection RTT).
- Private Key JWT (RFC 7523) instead of client_secret. Stronger but adds key-management complexity. Default to client_secret; revisit in Phase D.

---

## Appendix A — Concrete LiteLLM hook diff (illustrative)

Current (`/opt/klai/litellm/klai_knowledge.py:478`):

```python
async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
    resp = await client.post(
        KNOWLEDGE_RETRIEVE_URL,
        json=retrieve_body,
        headers={"X-Internal-Secret": PORTAL_INTERNAL_SECRET} if PORTAL_INTERNAL_SECRET else {},
    )
```

After (Phase C-1):

```python
from klai_service_auth import ZitadelTokenClient

_token_client = ZitadelTokenClient(
    client_id=os.environ["KLAI_LITELLM_CLIENT_ID"],
    client_secret=os.environ["KLAI_LITELLM_CLIENT_SECRET"],
    token_url=os.environ["KLAI_OAUTH_TOKEN_URL"],
    scope="klai:internal:retrieval:query",
)

# inside the hook
try:
    auth_token = await _token_client.get_token()
    auth_header = {"Authorization": f"Bearer {auth_token}"}
except ServiceAuthError as exc:
    logger.warning("KlaiKnowledgeHook: jwt mint failed (%s) — falling back to legacy", exc)
    auth_header = {"X-Internal-Secret": PORTAL_INTERNAL_SECRET} if PORTAL_INTERNAL_SECRET else {}

async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
    resp = await client.post(KNOWLEDGE_RETRIEVE_URL, json=retrieve_body, headers=auth_header)
```

After Phase C-1 cleanup (REQ-5 7-day soak passed):

```python
auth_token = await _token_client.get_token()
async with httpx.AsyncClient(timeout=RETRIEVE_TIMEOUT) as client:
    resp = await client.post(
        KNOWLEDGE_RETRIEVE_URL,
        json=retrieve_body,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
```

## Appendix B — Sources

- OWASP Microservices Security Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Microservices_Security_Cheat_Sheet.html
- innoq, "Updating OWASP's Microservice Security Cheat Sheet: Authentication Patterns" (Aug 2025) — https://www.innoq.com/en/blog/2025/08/owasp-microservice-security-cheat-sheet-update-authentication-patterns/
- RFC 6749 §4.4 OAuth 2.0 Client Credentials grant — https://datatracker.ietf.org/doc/html/rfc6749
- Zitadel — Configure client credential authentication for service users — https://zitadel.com/docs/guides/integrate/service-users/client-credentials
- Zitadel — OAuth Client Credentials for Service Accounts — https://zitadel.com/docs/guides/integrate/service-accounts/client-credentials
- Zitadel — Private Key JWT Auth for Service Accounts — https://zitadel.com/docs/guides/integrate/service-accounts/private-key-jwt
