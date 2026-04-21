# SPEC-SEC-012: Acceptance Criteria

Acceptance criteria are expressed in EARS format (Easy Approach to Requirements Syntax):
- **Ubiquitous:** `THE <system> SHALL ...`
- **Event-driven:** `WHEN <trigger> THE <system> SHALL ...`
- **State-driven:** `WHILE <state> THE <system> SHALL ...`
- **Unwanted behavior:** `IF <condition> THEN THE <system> SHALL ...`
- **Optional:** `WHERE <feature> THE <system> SHALL ...`

All criteria below are verified either via automated test or via deploy-time check.

---

## AC-1: scribe-api fails to start without audience configured

**WHEN** `scribe-api` starts **IF** the environment variable `SCRIBE_ZITADEL_AUDIENCE` is not set, is an empty string, or contains only whitespace **THEN** the service **SHALL** fail to start with a pydantic-settings validation error whose message clearly identifies `SCRIBE_ZITADEL_AUDIENCE` as the missing or empty required variable.

Verified by: startup test that launches the app with the variable unset / empty / whitespace and asserts a `ValidationError` (or equivalent import-time exception) is raised and the FastAPI app is not importable. No HTTP server must come up.

---

## AC-2: research-api fails to start without audience configured

**WHEN** `research-api` starts **IF** the environment variable `RESEARCH_API_ZITADEL_AUDIENCE` is not set, is an empty string, or contains only whitespace **THEN** the service **SHALL** fail to start with a pydantic-settings validation error whose message clearly identifies `RESEARCH_API_ZITADEL_AUDIENCE` as the missing or empty required variable.

Verified by: startup test as in AC-1, mirrored for research-api.

---

## AC-3: scribe-api rejects tokens with mismatching audience

**WHILE** `scribe-api` is running with `SCRIBE_ZITADEL_AUDIENCE` set to app-id `A` **IF** a request arrives with a Bearer token whose `aud` claim is app-id `B` (`B != A`) **THEN** the service **SHALL** respond with HTTP 401 and the existing error body (`{"detail": "Ongeldig of verlopen token"}`).

Verified by: a pytest case that signs a test JWT with the mock JWKS key and an `aud` claim of `"other-app-id"`, issues the request, and asserts status 401.

---

## AC-4: research-api rejects tokens with mismatching audience

**WHILE** `research-api` is running with `RESEARCH_API_ZITADEL_AUDIENCE` set to app-id `A` **IF** a request arrives with a Bearer token whose `aud` claim is app-id `B` (`B != A`) **THEN** the service **SHALL** respond with HTTP 401 and the existing error body (`{"detail": "Ongeldig of verlopen token"}`).

Verified by: pytest case mirroring AC-3 for research-api.

---

## AC-5: scribe-api accepts tokens with matching audience

**WHILE** `scribe-api` is running with `SCRIBE_ZITADEL_AUDIENCE` set to app-id `A` **WHEN** a request arrives with a Bearer token whose `aud` claim is app-id `A`, whose `iss` matches the configured issuer, and whose signature is valid under the current JWKS **THEN** the service **SHALL** authenticate the request and route to the downstream handler (no 401 from the auth dependency).

Verified by: updating existing authenticated-endpoint tests to mint tokens with the correct `aud` and asserting they still pass. These tests also cover REQ-4.1.

---

## AC-6: research-api accepts tokens with matching audience

**WHILE** `research-api` is running with `RESEARCH_API_ZITADEL_AUDIENCE` set to app-id `A` **WHEN** a request arrives with a valid Bearer token whose `aud` claim is app-id `A` **THEN** the service **SHALL** authenticate the request and return `CurrentUser` as today, provided the user exists in `portal_users`.

Verified by: updating existing research-api authenticated-endpoint tests to mint tokens with the correct `aud` and asserting they still pass.

---

## AC-7: scribe-api no longer contains `verify_aud: False` anywhere

**THE** `klai-scribe/scribe-api/` codebase **SHALL** contain no occurrence of the literal `"verify_aud": False` or `"verify_aud":False` in any Python source, configuration, or test file after this SPEC is implemented.

Verified by: repository grep run as part of the acceptance sweep; zero matches expected.

---

## AC-8: research-api no longer contains `verify_aud: False` anywhere

**THE** `klai-focus/research-api/` codebase **SHALL** contain no occurrence of the literal `"verify_aud": False` or `"verify_aud":False`, and no conditional branch of the shape `if settings.zitadel_api_audience: ... else: ...` in `app/core/auth.py` after this SPEC is implemented.

Verified by: repository grep plus a manual read of the updated `_decode_token`.

---

## AC-9: research-api decode call is unconditional in audience verification

**WHEN** `_decode_token` in `klai-focus/research-api/app/core/auth.py` is invoked **THE** function **SHALL** always pass `audience=settings.research_api_zitadel_audience` to `jose.jwt.decode`, with no surrounding `if`-branch that can skip or replace the audience parameter.

Verified by: code review of the diff — the `decode_kwargs` dict must contain `"audience": settings.research_api_zitadel_audience` unconditionally, and no `options={"verify_aud": ...}` entry must remain.

---

## AC-10: scribe-api decode call is unconditional in audience verification

**WHEN** `get_current_user_id` in `klai-scribe/scribe-api/app/core/auth.py` is invoked **THE** function **SHALL** call `jose.jwt.decode` with `audience=settings.scribe_zitadel_audience` as a direct keyword argument, and **SHALL NOT** pass any `options` argument that disables audience verification.

Verified by: code review of the diff.

---

## AC-11: Per-service distinct audience in infra config

**THE** encrypted config file `klai-infra/core-01/.env.sops` **SHALL** define two distinct variables, `SCRIBE_ZITADEL_AUDIENCE` and `RESEARCH_API_ZITADEL_AUDIENCE`, each holding a distinct Zitadel application/project id. The two plaintext values **SHALL NOT** be equal.

Verified by: after SOPS decrypt during the deploy step, a shell check asserts both variables are set and `"$SCRIBE_ZITADEL_AUDIENCE" != "$RESEARCH_API_ZITADEL_AUDIENCE"`.

---

## AC-12: Existing tests updated, not skipped

**WHEN** the test suites for scribe-api and research-api are executed **THEN** all previously-passing authenticated-endpoint tests **SHALL** continue to pass using tokens signed with the correct `aud` claim. No test **SHALL** be marked `skip` or `xfail` as a workaround for the audience check.

Verified by: CI output for both services shows the same or higher number of passing tests as before the change, with zero new skips attributable to this SPEC.

---

## Quality Gates

- [ ] `ruff check` passes for both services
- [ ] `pyright` (or `mypy`) passes for both services
- [ ] `pytest` passes for both services with the new cases added (mismatching-aud 401, missing-env startup-fail)
- [ ] Repo-wide grep for `verify_aud.*False` returns zero matches in `klai-scribe/` and `klai-focus/research-api/`
- [ ] Deploy runbook / env checklist updated with both new variables
- [ ] SOPS file round-trips (decrypt → check → encrypt-in-place) with both variables present
