# klai-service-auth

OAuth 2.0 Client Credentials token client for Klai inter-service authentication.

Implements SPEC-SEC-SERVICE-AUTH-001. Each Klai service that calls another internal service
mints a short-lived JWT against Zitadel's `/oauth/v2/token` endpoint and sends it as
`Authorization: Bearer <jwt>`. Receivers validate the JWT signature against Zitadel JWKS
and check the scope claim.

## Quickstart

```python
from klai_service_auth import ZitadelTokenClient, ServiceAuthError

token_client = ZitadelTokenClient(
    client_id=os.environ["KLAI_LITELLM_CLIENT_ID"],
    client_secret=os.environ["KLAI_LITELLM_CLIENT_SECRET"],
    token_url=os.environ["KLAI_OAUTH_TOKEN_URL"],
    scope="klai:internal:retrieval:query",
)

# Once per outbound call:
try:
    bearer = await token_client.get_token()
except ServiceAuthError as exc:
    # IdP unavailable / bad credentials. Decide: fail-closed or fall back.
    logger.warning("token mint failed: %s", exc)
    raise

async with httpx.AsyncClient() as c:
    await c.post(url, headers={"Authorization": f"Bearer {bearer}"}, json=body)
```

## Behaviour

- **Caching**: token is cached and reused until 80% of its advertised TTL has elapsed.
- **Concurrency**: single `asyncio.Lock` prevents thundering-herd token mints during cache miss.
- **Fail-fast**: empty `client_id` or `client_secret` raises at construction time —
  no silent fallback to legacy auth.
- **Logging**: structured events `service_auth_token_minted`, `service_auth_token_mint_failed`,
  `service_auth_token_cache_hit`. All include `client_id` (NOT secret).

## Why this exists

See SPEC-SEC-SERVICE-AUTH-001 in `.moai/specs/`. Short version: the previous shared
`X-Internal-Secret` pattern drifted in production (2026-05-01 incident: silent KB
augmentation failure on Voys tenant), has no caller identity for audit, and gives
no per-endpoint authorization.

## Embedding into a service

1. Add `"klai-service-auth"` to the service's `pyproject.toml` dependencies.
2. Add path-dep: `klai-service-auth = { path = "../klai-libs/service-auth" }`.
3. Add `KLAI_<SERVICE>_CLIENT_ID`, `KLAI_<SERVICE>_CLIENT_SECRET`, `KLAI_OAUTH_TOKEN_URL`
   to the service's `deploy/docker-compose.yml` `environment:` block.
4. Create the matching service account in Zitadel via
   `klai-infra/scripts/zitadel-create-service-account.py --name svc-<service>`.
5. SOPS-encrypt the client_secret into `klai-infra/core-01/.env.sops` BEFORE deploying
   any code that reads it (per SPEC-SEC-SERVICE-AUTH-001 REQ-7).
