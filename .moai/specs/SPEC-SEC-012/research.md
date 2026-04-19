# SPEC-SEC-012: Research Notes

## Audit findings summary

### F-002 — scribe-api hardcodes `verify_aud: False`

Source: Phase 3 security audit 2026-04-19, `.moai/audit/04-tenant-isolation.md` (F-002), rolled up in `.moai/audit/99-fix-roadmap.md` under `## SEC-012 — JWT audience verification mandatory`.

Finding: `klai-scribe/scribe-api/app/core/auth.py` (lines 64-70 at audit time) calls:

```
payload = jwt.decode(
    token,
    key,
    algorithms=["RS256"],
    issuer=settings.zitadel_issuer,
    options={"verify_aud": False},
)
```

`options={"verify_aud": False}` tells `python-jose` to skip audience verification entirely. The token's `aud` claim is not consulted. Any token signed by the configured Zitadel issuer is accepted — including tokens minted for other applications in the same Zitadel tenant (portal, LibreChat, etc.). Issuer verification alone is insufficient for multi-application tenants: it proves only that the tenant signed the token, not that the token was intended for scribe-api.

Classification: HIGH — enables cross-application token reuse against scribe-api.

### F-004 — research-api audience check is opt-in

Source: same audit, `.moai/audit/04-tenant-isolation.md` (F-004).

Finding: `klai-focus/research-api/app/core/auth.py` (lines 67-74 at audit time) makes audience verification conditional:

```
if settings.zitadel_api_audience:
    decode_kwargs["audience"] = settings.zitadel_api_audience
else:
    logger.error(
        "ZITADEL_API_AUDIENCE not set — JWT audience verification is DISABLED. "
        "Set RESEARCH_API_ZITADEL_AUDIENCE in .env to the Zitadel project ID."
    )
    decode_kwargs["options"] = {"verify_aud": False}
```

A missing env var silently degrades the service to F-002-level. The `logger.error` line surfaces the issue in logs, but the runtime behavior is still "accept any token from the issuer." In practice, if `ZITADEL_API_AUDIENCE` was ever unset in production (missed during an env update, typo, rotated but not redeployed), the service would accept cross-application tokens without any 5xx or visible failure.

The inconsistency between the setting name referenced in the error message (`RESEARCH_API_ZITADEL_AUDIENCE`) and the field actually read (`zitadel_api_audience`) reinforces the case for removing the field and renaming it consistently.

Classification: HIGH — silent fallback hides misconfiguration; remediation removes the fallback.

## python-jose decode semantics (reference)

`jose.jwt.decode(token, key, *, algorithms, audience=None, issuer=None, subject=None, access_token=None, options=None)` — verification behavior:

- When `audience` is supplied as a non-empty string: the library reads the token's `aud` claim, which may be a single string or a list of strings, and raises `JWTClaimsError` (a subclass of `JWTError`) when the supplied audience is not present. This is the desired behavior for both services.
- When `audience` is omitted **and** `options["verify_aud"] == True` (the default): the library will raise if the token has an `aud` claim but no expected audience was supplied (defensive default). However, the current research-api code path sets `verify_aud: False` in the fallback branch, defeating even this default.
- When `options={"verify_aud": False}` is set: the library skips audience verification regardless of what the token contains. This is the vulnerable path.

Consequence: passing `audience=<expected app id>` without any `options` override gives the exact behavior this SPEC requires — a mismatched `aud` raises `JWTError`, which both services already translate to `HTTPException(401)`.

Reference code path: `jose/jwt.py::_validate_claims` → `_validate_aud`.

## Why portal-api is out of scope

portal-api does not locally decode Zitadel access tokens with `python-jose`. Instead, it uses **Zitadel token introspection** — a server-to-server call to Zitadel's introspection endpoint, which returns `active=true`/`false` plus claims. Audience semantics on the introspection path are a property of the introspection response and of Zitadel's configuration for the API application that owns the introspection credentials, not of an in-process `jwt.decode` call. The remediation pattern in this SPEC (remove `verify_aud: False`, add `audience=...`, add pydantic validator) does not map onto introspection-based auth and would be a no-op there.

The same reasoning applies to `klai-connector`, which also uses introspection. If a gap is found on the introspection path during a later audit, it gets its own SPEC — do not conflate the two mechanisms.

## Related Klai rules and pitfalls

- `.claude/rules/klai/pitfalls/process-rules.md` — `follow-loaded-procedures`: SOPS workflow is decrypt → modify → encrypt-in-place → mv. Do not improvise when adding the new encrypted variables.
- `.claude/rules/klai/pitfalls/process-rules.md` — `search-broadly-when-changing`: renaming `zitadel_api_audience` to `research_api_zitadel_audience` has unbounded blast radius. Grep for every case variant (`ZITADEL_API_AUDIENCE`, `zitadel_api_audience`) across the research-api repo, tests, deploy scripts, and env files to catch stale references.
- `.claude/rules/klai/infra/observability.md` — after deploy, verify via VictoriaLogs that no 401 spikes appear that would indicate a caller using the wrong Zitadel app. Query `service:scribe-api AND status:401` and `service:research-api AND status:401` around the deploy window.

## Open questions for ops (resolve before deploy, not blocking the SPEC)

1. What are the two distinct Zitadel application/project IDs to use for `SCRIBE_ZITADEL_AUDIENCE` and `RESEARCH_API_ZITADEL_AUDIENCE`? Confirm via the Zitadel admin console and record the mapping.
2. Are there any existing callers of scribe-api or research-api that currently use a token minted for a different Zitadel app (e.g., a shared portal token)? If yes, fix the caller **before** this SPEC rolls out — or the deploy will 401 that caller.
3. Does Zitadel's current app configuration already emit the `aud` claim in access tokens for both apps? Default yes, but confirm with a quick `jwt.io` or curl-based introspection of a sample token prior to deploy.
