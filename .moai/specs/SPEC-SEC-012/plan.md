# SPEC-SEC-012: Implementation Plan

This plan covers four work streams: (1) scribe-api code + config, (2) research-api code + config, (3) test updates and additions, (4) infra variables via SOPS workflow. Order matters: code changes and env variables must land together. Deploying the code change without the env var will brick the service on startup (by design — that is the guard rail).

---

## Work stream 1 — scribe-api hardening

Path: `klai-scribe/scribe-api/`

### Task 1.1 — Add `scribe_zitadel_audience` setting with validator

File: `klai-scribe/scribe-api/app/core/config.py` (the pydantic-settings module loaded by `app/core/auth.py` via `from app.core.config import settings`).

Change outline:
- Add required field `scribe_zitadel_audience: str` with no default. Environment variable name: `SCRIBE_ZITADEL_AUDIENCE`.
- Add a `@field_validator("scribe_zitadel_audience")` (pydantic v2) or `@validator` (v1) that strips the value and raises `ValueError("SCRIBE_ZITADEL_AUDIENCE must be set and non-empty")` when the stripped value is empty.
- Do not provide any fallback to a pre-existing generic audience field.

### Task 1.2 — Harden `auth.py` decode call

File: `klai-scribe/scribe-api/app/core/auth.py` (current lines 64-70).

Change outline:
- Remove `options={"verify_aud": False}` from the `jwt.decode(...)` call.
- Add `audience=settings.scribe_zitadel_audience` as an explicit keyword argument.
- Leave the surrounding `try/except JWTError` intact — a mismatching audience raises `JWTError` which is already translated to `HTTPException(401)`.

Diff shape (for reviewer clarity, not actual code):
```
         payload = jwt.decode(
             token,
             key,
             algorithms=["RS256"],
             issuer=settings.zitadel_issuer,
-            options={"verify_aud": False},
+            audience=settings.scribe_zitadel_audience,
         )
```

### Task 1.3 — Verify scribe-api locally

- `uv run ruff check .`
- `uv run --with pyright pyright`
- `uv run pytest` (after Task 3 updates land)

---

## Work stream 2 — research-api hardening

Path: `klai-focus/research-api/`

### Task 2.1 — Replace `zitadel_api_audience` with required `research_api_zitadel_audience`

File: `klai-focus/research-api/app/core/config.py`.

Change outline:
- Remove the optional `zitadel_api_audience` field entirely (it is the root cause of the silent fallback).
- Add required field `research_api_zitadel_audience: str` with no default. Environment variable: `RESEARCH_API_ZITADEL_AUDIENCE`.
- Add a `@field_validator` that raises `ValueError` when missing, empty, or whitespace-only.

### Task 2.2 — Remove conditional audience branch in `_decode_token`

File: `klai-focus/research-api/app/core/auth.py` (current lines 63-76).

Change outline:
- Delete the `if settings.zitadel_api_audience: ... else: ... decode_kwargs["options"] = {"verify_aud": False}` branch.
- Build `decode_kwargs` with `audience=settings.research_api_zitadel_audience` unconditionally.
- Remove the `logger.error(...)` line inside the former `else` branch — it is no longer reachable.

Diff shape:
```
-        decode_kwargs: dict = {
-            "algorithms": ["RS256"],
-            "issuer": settings.zitadel_issuer,
-        }
-        if settings.zitadel_api_audience:
-            decode_kwargs["audience"] = settings.zitadel_api_audience
-        else:
-            logger.error(
-                "ZITADEL_API_AUDIENCE not set — JWT audience verification is DISABLED. "
-                "Set RESEARCH_API_ZITADEL_AUDIENCE in .env to the Zitadel project ID."
-            )
-            decode_kwargs["options"] = {"verify_aud": False}
+        decode_kwargs: dict = {
+            "algorithms": ["RS256"],
+            "issuer": settings.zitadel_issuer,
+            "audience": settings.research_api_zitadel_audience,
+        }
```

### Task 2.3 — Verify research-api locally

- `uv run ruff check .`
- `uv run --with pyright pyright`
- `uv run pytest` (after Task 3 updates land)

---

## Work stream 3 — Tests

### Task 3.1 — Update existing auth tests in scribe-api

Where applicable (likely `klai-scribe/scribe-api/tests/` — confirm paths at implementation time):
- Update the JWT factory / fixture used by existing tests to include a valid `aud` claim matching the test settings' `scribe_zitadel_audience`.
- Any test that previously relied on the absence of audience verification MUST be rewritten to mint a correctly-scoped token — do not `skip` or `xfail`.

### Task 3.2 — Add new negative tests in scribe-api

- **Token confusion test:** sign a JWT with a valid issuer + signature but with `aud="other-app-id"`; issue a request to an authenticated endpoint; assert HTTP 401.
- **Startup failure test:** instantiate `Settings` (or import the app) with the `SCRIBE_ZITADEL_AUDIENCE` env var unset, empty, and whitespace-only; assert a `ValidationError` (pydantic v2) or `pydantic.ValidationError`/`ValueError` is raised in each case. Use `monkeypatch.delenv` and `monkeypatch.setenv` as needed.

### Task 3.3 — Update existing auth tests in research-api

Mirror Task 3.1 for research-api. The test fixtures in `klai-focus/research-api/tests/` (path to confirm) that mint mock Zitadel tokens MUST issue them with `aud=research_api_zitadel_audience` from test settings.

### Task 3.4 — Add new negative tests in research-api

Mirror Task 3.2 for research-api, using `RESEARCH_API_ZITADEL_AUDIENCE` as the missing variable under test.

### Task 3.5 — Repo-wide grep

After all other tasks land, run:
```
rg -n "verify_aud" klai-scribe klai-focus/research-api
```
Expected output: zero matches. Any remaining hit is a blocker.

---

## Work stream 4 — Infra (SOPS)

Path: `klai-infra/core-01/.env.sops`

### Task 4.1 — Add the two variables via the SOPS workflow

Follow the repository's documented SOPS procedure (decrypt → modify → encrypt-in-place → mv). Do not improvise with redirects.

Variables to add:
- `SCRIBE_ZITADEL_AUDIENCE=<scribe Zitadel app id>`
- `RESEARCH_API_ZITADEL_AUDIENCE=<research-api Zitadel app id>`

The two values MUST be distinct Zitadel application/project ids. Ops confirms the mapping by cross-checking with the Zitadel admin console (or the recorded app registration notes) before encrypting.

### Task 4.2 — Remove the legacy `ZITADEL_API_AUDIENCE` entry if present

If the encrypted file currently contains an entry under the old generic name `ZITADEL_API_AUDIENCE` (from research-api's previous opt-in state), remove it as part of the same SOPS round-trip to avoid confusion.

### Task 4.3 — Update deploy runbook / env checklist

Whichever document lists required env vars for core-01 deploy (commonly referenced by the release checklist) — add both new variables and a one-line note explaining that each service requires its own distinct audience.

---

## Sequencing and safety

1. Land code changes (Work stream 1 + 2) and tests (Work stream 3) together in the same PR. Do **not** merge partial work: a merged code change without the env var guard in CI staging environment will surface a startup failure early — that is intended.
2. Before deploying to core-01, complete Work stream 4 and verify by dry-running the deploy (decrypt + `docker compose config`) to confirm both variables are exported into the services.
3. Deploy both services in the same release. There is no mixed-state that is safe: a still-unverifying service is the whole problem this SPEC fixes.
4. If Zitadel does not yet emit the expected `aud` claim for either app (ops pre-check), fix Zitadel **first**. Do not add a `verify_aud: False` fallback.

## Files changed (summary)

| File | Change |
|------|--------|
| `klai-scribe/scribe-api/app/core/config.py` | Add required `scribe_zitadel_audience` + validator |
| `klai-scribe/scribe-api/app/core/auth.py` | Remove `verify_aud: False`; pass `audience=settings.scribe_zitadel_audience` |
| `klai-focus/research-api/app/core/config.py` | Remove optional `zitadel_api_audience`; add required `research_api_zitadel_audience` + validator |
| `klai-focus/research-api/app/core/auth.py` | Remove conditional branch; pass `audience=...` unconditionally |
| `klai-scribe/scribe-api/tests/...` | Update fixtures; add token-confusion + startup-fail tests |
| `klai-focus/research-api/tests/...` | Update fixtures; add token-confusion + startup-fail tests |
| `klai-infra/core-01/.env.sops` | Add `SCRIBE_ZITADEL_AUDIENCE` and `RESEARCH_API_ZITADEL_AUDIENCE`; remove legacy `ZITADEL_API_AUDIENCE` |
| Deploy runbook / env checklist (path per repo convention) | List both new variables as required |

## Rollback

- Code: revert the PR. Services restart with the old behavior (which is exactly the vulnerability — so rollback is intended only if deploy itself fails for unrelated reasons, not as a response to a rejected-token incident).
- Env vars: the new variables are additive in SOPS; removing them will re-trigger the startup validator and block the service from coming up. That is intentional — there is no safe way to run these services without audience verification.
- If a legitimate caller is rejected post-deploy because the token was minted for the wrong Zitadel app, the fix is at the caller or in Zitadel app registration, not in this SPEC.
