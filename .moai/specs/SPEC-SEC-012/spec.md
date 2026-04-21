---
id: SPEC-SEC-012
version: 0.2.0
status: implemented (research-api)
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: high
---

# SPEC-SEC-012: Mandatory JWT Audience Verification

## HISTORY

### v0.2.0 (2026-04-19)
- research-api implementation landed: pydantic `model_validator` rejects empty `RESEARCH_API_ZITADEL_AUDIENCE` at startup; `auth._decode_bearer` now always passes `audience=settings.zitadel_api_audience` (no conditional fallback, no `verify_aud: False` branch). RESEARCH_API_ZITADEL_AUDIENCE already set in SOPS + live on core-01.
- scribe-api portion: superseded by SPEC-VEXA-003 rebuild (scribe now uses internal-secret + vexa-issued tokens, not Zitadel access tokens directly). This SPEC marked implemented against its remaining research-api scope; scribe/docs-app audience questions tracked under SEC-020.

### v0.1.0 (2026-04-19)
- Initial draft. Combines audit findings F-002 (scribe-api hardcodes `verify_aud: False`) and F-004 (research-api audience check is opt-in) into a single remediation SPEC.
- Scope limited to services that decode Zitadel JWTs locally via `python-jose`. Services using Zitadel introspection (portal-api, klai-connector) are explicitly out of scope.

---

## Goal

Make JWT audience (`aud`) claim verification mandatory and unconditional in every Klai service that accepts Zitadel access tokens by locally decoding them. Today, two services (`klai-scribe/scribe-api` and `klai-focus/research-api`) accept any valid Zitadel token signed by the configured issuer, regardless of which Zitadel application it was minted for. That means an access token issued for one application (for example, the portal or LibreChat) is silently accepted as a valid token by scribe-api and research-api, enabling cross-application token reuse. This SPEC closes that gap by:

1. Removing `options={"verify_aud": False}` from scribe-api's JWT decode call and replacing it with an explicit `audience=...` argument.
2. Removing research-api's conditional audience-verification branch (`if settings.zitadel_api_audience: ... else: verify_aud=False`) and making the audience parameter unconditional.
3. Adding pydantic-settings validators that fail service startup when the required audience environment variable is missing, empty, or whitespace-only — there must be no silent fallback.
4. Adding per-service environment variables (`SCRIBE_ZITADEL_AUDIENCE`, `RESEARCH_API_ZITADEL_AUDIENCE`) stored in SOPS-encrypted `.env.sops`, each pointing to a distinct Zitadel application's project/client ID.

---

## Success Criteria

- Both `scribe-api` and `research-api` call `jose.jwt.decode(..., audience=<configured app id>)` on every request. No code path allows bypassing audience verification.
- Neither service accepts a token whose `aud` claim does not match its configured audience. Cross-application token reuse (token for app A → request against service B) returns HTTP 401.
- Both services fail to start (explicit exception during pydantic-settings load) when their audience environment variable is missing, empty, or whitespace-only.
- Existing test suites for both services pass: mocks have been updated to issue tokens with the correct `aud` value.
- New tests cover (a) token-confusion rejection and (b) startup-failure when audience is unset.
- `klai-infra/core-01/.env.sops` contains encrypted values for `SCRIBE_ZITADEL_AUDIENCE` and `RESEARCH_API_ZITADEL_AUDIENCE`. Infra deployment documentation references both variables.
- Deployment note added: before promoting the release, Zitadel is confirmed to issue tokens with the correct `aud` claim per application.

---

## Environment

- **Services in scope (2):**
  - `klai-scribe/scribe-api` — Python 3.12, FastAPI, `python-jose[cryptography]`, `pydantic-settings`, JWT decode in `app/core/auth.py`.
  - `klai-focus/research-api` — Python 3.12, FastAPI, `python-jose[cryptography]`, `pydantic-settings`, JWT decode in `app/core/auth.py`.
- **Identity provider:** Zitadel (EU region), issuer configured per service via `ZITADEL_ISSUER`. JWKS fetched from `{issuer}/oauth/v2/keys`, in-memory cached with single forced refresh on `kid` miss.
- **Token format:** RS256-signed access tokens. Each Zitadel application has a distinct project/client id that is emitted as the `aud` claim.
- **Config layer:** `pydantic-settings` `BaseSettings` with `.env` / `.env.sops` loader. SOPS with age keys as documented in `klai-infra/core-01/`.
- **Deployment:** Docker Compose on `core-01`. Secrets materialized from SOPS during deploy.

## Assumptions

- Zitadel is configured to issue the `aud` claim for every access token, set to the project/client ID of the app that requested the token. This is already the default behavior for access tokens produced via the API config used by Klai apps.
- Each Klai service that locally decodes tokens is registered as its own Zitadel application (or reuses a shared API application id that is known to be distinct per service). Ops verifies this mapping before deploy.
- No external caller intentionally reuses a token across applications today. Any reuse surfaced after the rollout is a client bug in the caller and MUST be fixed caller-side — not by reintroducing `verify_aud: False`.
- `python-jose` behavior is stable: supplying `audience=<string>` causes `jwt.decode` to raise `JWTError` when the token's `aud` claim (string or list) does not contain the expected value.

---

## Out of Scope

- **portal-api** — uses Zitadel token **introspection** (server-to-server endpoint check), a different mechanism that does not involve local `python-jose` decoding. Introspection checks token validity at Zitadel directly; audience verification semantics on that path are handled by Zitadel's introspection response and any extra checks are tracked separately if needed.
- **klai-connector** — uses Zitadel introspection (server-to-server check), same category as portal-api. Separate remediation SPEC if a hardening gap is found during audit of that path.
- **Other services that do NOT accept Zitadel end-user tokens** (mailer, knowledge-ingest, retrieval-api internal callers, etc.) — they authenticate using `X-Internal-Secret` and are not affected by this SPEC.
- Any change to Zitadel itself (app registration, token lifetime, claim configuration). Zitadel-side changes are handled by ops as prerequisites, not as part of this SPEC.
- Rotation of signing keys, JWKS cache eviction strategy, or issuer verification behavior — those are already correctly implemented and unchanged by this SPEC.
- Backward-compatible "soft-fail" or "audit-only" modes. The change is a hard cutover: audience verification is ON from deploy, full stop.

---

## Security Findings Addressed

This SPEC remediates two findings from the Phase 3 security audit (`.moai/audit/`) completed 2026-04-19:

- **F-002** — `klai-scribe/scribe-api/app/core/auth.py` lines 64-70 call `jose.jwt.decode(..., options={"verify_aud": False})`. The audience claim is never checked. Any valid Zitadel token signed by the configured issuer is accepted by scribe-api, regardless of the app for which it was minted.
- **F-004** — `klai-focus/research-api/app/core/auth.py` lines 67-74 make audience verification conditional on `settings.zitadel_api_audience` being set: when it is not set, the code logs an error and proceeds with `options={"verify_aud": False}`. In production, the variable being unset silently degrades the security posture to F-002-level.

Audit cross-references:
- `.moai/audit/04-tenant-isolation.md` — F-002 (scribe-api hardcoded opt-out) and F-004 (research-api opt-in).
- `.moai/audit/99-fix-roadmap.md`, section `## SEC-012 — JWT audience verification mandatory` — remediation tracking entry that this SPEC fulfills.

---

## Threat Model

A Zitadel tenant that hosts Klai typically registers multiple applications — for example, the portal web app, the LibreChat client, and each locally-decoding API service. Each application receives its own client/project ID, and access tokens are minted per application. Without `aud` verification, scribe-api and research-api accept any token signed by the tenant's issuer, meaning an access token obtained for application A (e.g., the portal) is accepted as a valid credential by service B (e.g., scribe-api) — even though the user never consented to that application and the token's scope was never intended for it. This breaks the application-boundary trust model that multi-app Zitadel tenants rely on. Mandatory `aud` verification restores the invariant that a token issued for app A only grants access to the resources owned by app A.

---

## Requirements

### REQ-1: scribe-api MUST verify JWT audience unconditionally

**REQ-1.1:** The `jwt.decode` call in `klai-scribe/scribe-api/app/core/auth.py` (currently at line 64-70) SHALL pass `audience=settings.scribe_zitadel_audience` as an explicit keyword argument. The `options={"verify_aud": False}` argument SHALL be removed entirely.

**REQ-1.2:** The scribe-api `Settings` class (pydantic-settings) SHALL define a `scribe_zitadel_audience: str` field with no default value. Populated via the environment variable `SCRIBE_ZITADEL_AUDIENCE`.

**REQ-1.3:** The scribe-api `Settings` class SHALL include a `pydantic` field validator (e.g., `@field_validator("scribe_zitadel_audience")`) that raises `ValueError` when the value is missing, empty string, or contains only whitespace. Service startup MUST fail with a clear error message identifying `SCRIBE_ZITADEL_AUDIENCE` as the missing variable.

**REQ-1.4:** When the `aud` claim of a received token does not match `scribe_zitadel_audience`, scribe-api SHALL reject the request with HTTP 401 and the existing `Ongeldig of verlopen token` error body. The rejection path is the existing `JWTError` → `HTTPException(401)` branch.

### REQ-2: research-api MUST verify JWT audience unconditionally

**REQ-2.1:** The `_decode_token` function in `klai-focus/research-api/app/core/auth.py` (currently at lines 63-76) SHALL pass `audience=settings.research_api_zitadel_audience` to `jwt.decode`. The conditional `if settings.zitadel_api_audience: ... else: decode_kwargs["options"] = {"verify_aud": False}` branch SHALL be removed entirely.

**REQ-2.2:** The research-api `Settings` class (pydantic-settings) SHALL define a `research_api_zitadel_audience: str` field with no default value. Populated via the environment variable `RESEARCH_API_ZITADEL_AUDIENCE`. The pre-existing optional `zitadel_api_audience` setting SHALL be removed in the same change to eliminate dead code.

**REQ-2.3:** The research-api `Settings` class SHALL include a field validator that raises `ValueError` when `research_api_zitadel_audience` is missing, empty, or whitespace-only. Service startup MUST fail with a clear error message identifying `RESEARCH_API_ZITADEL_AUDIENCE` as the missing variable.

**REQ-2.4:** When the `aud` claim of a received token does not match `research_api_zitadel_audience`, research-api SHALL reject the request with HTTP 401 via the existing `JWTError` → `HTTPException(401)` branch.

### REQ-3: Configuration and validation

**REQ-3.1:** The two audience environment variables (`SCRIBE_ZITADEL_AUDIENCE` and `RESEARCH_API_ZITADEL_AUDIENCE`) SHALL be stored encrypted in `klai-infra/core-01/.env.sops` using the repo's SOPS workflow. The plaintext values correspond to the distinct Zitadel application IDs for scribe-api and research-api respectively.

**REQ-3.2:** The two variables SHALL be distinct. Re-using a single shared audience across multiple services is explicitly disallowed because it would re-enable cross-service token reuse between Klai services.

**REQ-3.3:** Documentation for ops (whatever deploy runbook references `.env.sops` for core-01) SHALL list both new variables as required for the deploy of this SPEC. Missing variable → startup fails → rollback.

**REQ-3.4:** No code path in either service SHALL read a generic audience variable as a silent fallback. The service's own variable (`SCRIBE_*` or `RESEARCH_API_*`) is the single source of truth for that service.

### REQ-4: Test coverage

**REQ-4.1:** Existing auth tests in both services SHALL be updated to mint test tokens with the correct `aud` claim value (matching each service's configured audience). Tests that relied on the absence of audience verification SHALL be rewritten rather than left passing by accident.

**REQ-4.2:** A new test per service SHALL assert that a token minted with a mismatching `aud` claim (simulating a token for a different Zitadel application) results in HTTP 401.

**REQ-4.3:** A new test per service SHALL assert that the service fails to start (pydantic-settings `ValidationError` or equivalent) when the audience environment variable is unset or empty.
