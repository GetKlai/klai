# Acceptance Criteria -- SPEC-SEC-WEBHOOK-001

EARS-format acceptance tests that MUST pass before SPEC-SEC-WEBHOOK-001 is considered
complete. Each item is verifiable against `klai-portal/backend/app/api/meetings.py`,
`webhooks.py`, `entrypoint.sh`, and the pydantic `Settings` class in `app/core/config.py`.

Maps back to REQ-1..REQ-5 in spec.md.

---

## AC-1: IP-range early-return is removed from Vexa webhook auth

- **WHEN** `_require_webhook_secret` in `klai-portal/backend/app/api/meetings.py` is inspected **THE** function body **SHALL NOT** contain any reference to `client_host.startswith(("172.", ...))` or any other IP/CIDR short-circuit. Authentication SHALL be determined solely by the Bearer-token comparison.
- **WHEN** a static grep of `meetings.py` for the strings `"172."`, `"10."`, `"192.168."`, `"startswith"`, and `"client_host"` runs **THE** result **SHALL** contain zero matches inside `_require_webhook_secret`.

## AC-2: Constant-time compare is preserved in Vexa webhook auth

- **WHEN** `_require_webhook_secret` rejects a request **THE** rejection path **SHALL** have been gated by `hmac.compare_digest(...)`, not by `!=`. The `hmac.compare_digest` call in `meetings.py:57` SHALL be preserved unchanged by this SPEC.

## AC-3: Unauthenticated POST from 172.x source returns 401

- **WHEN** a POST is made to `/api/bots/internal/webhook` with `X-Forwarded-For` set to `172.18.0.99` AND no `Authorization` header **THE** service **SHALL** return HTTP 401 with body containing `"detail": "Unauthorized"`.
- **WHEN** a POST is made with `X-Forwarded-For` set to `10.0.0.42` AND `Authorization: Bearer wrong-token` **THE** service **SHALL** return HTTP 401.
- Verification: new pytest at `klai-portal/backend/tests/test_meetings_webhook_auth.py` replacing the existing `test_require_webhook_secret_docker_network_trusted_without_bearer` test (currently asserting `no exception`, must be deleted or inverted).

## AC-4: uvicorn runs with --proxy-headers and a narrow allowlist

- **WHEN** `klai-portal/backend/entrypoint.sh` is inspected **THE** final `exec uvicorn ...` line **SHALL** contain the flag `--proxy-headers` AND the flag `--forwarded-allow-ips=<ip-or-cidr>` where `<ip-or-cidr>` is the Caddy container IP (or a `/32`), sourced from an env var such as `CADDY_CONTAINER_IP`.
- **WHEN** the allowlist value is inspected **THE** value **SHALL NOT** be `*`, `0.0.0.0/0`, `172.18.0.0/16`, or any other broad range. A single `/32` or a single plain IP is acceptable.
- **WHEN** the portal-api container starts against a running Caddy AND a webhook POST arrives with a legitimate `X-Forwarded-For: 8.8.8.8` from Caddy **THE** handler **SHALL** observe `request.client.host == "8.8.8.8"` (the real external caller), not the Caddy container IP.

## AC-5: Empty MONEYBIRD_WEBHOOK_TOKEN refuses to start

- **WHEN** `Settings()` is instantiated with `MONEYBIRD_WEBHOOK_TOKEN=""` (empty string) **THE** constructor **SHALL** raise `ValueError` with a message containing `MONEYBIRD_WEBHOOK_TOKEN`.
- **WHEN** `Settings()` is instantiated with `MONEYBIRD_WEBHOOK_TOKEN="   "` (whitespace only) **THE** constructor **SHALL** raise `ValueError`.
- **WHEN** the portal-api container is started against an env file that omits `MONEYBIRD_WEBHOOK_TOKEN` entirely **THE** uvicorn process **SHALL** abort before binding port 8010, visible in `docker logs` as a ValueError traceback. Verification: deliberately unset the var in a test compose override and observe container exit.
- Verification: pytest that calls `Settings(moneybird_webhook_token="")` in a `with pytest.raises(ValueError, match="MONEYBIRD_WEBHOOK_TOKEN"):` block.

## AC-6: Moneybird handler uses constant-time compare

- **WHEN** `klai-portal/backend/app/api/webhooks.py` is inspected **THE** token-comparison line inside `moneybird_webhook` **SHALL** use `hmac.compare_digest(...)` with both sides encoded as bytes. The `token != settings.moneybird_webhook_token` comparison (current line 26) SHALL be replaced.
- **WHEN** the handler is inspected **THE** surrounding guard `if settings.moneybird_webhook_token:` (current line 24) **SHALL NOT** exist -- the token is now always required by virtue of REQ-3.1 enforcing fail-closed startup.

## AC-7: Moneybird auth failure returns HTTP 401, not 200

- **WHEN** a POST is made to `/api/webhooks/moneybird` with JSON body `{"webhook_token": "wrong"}` **THE** service **SHALL** return HTTP 401 with body `{"detail": "Unauthorized"}`. It **SHALL NOT** return HTTP 200.
- **WHEN** the handler rejects the call **THE** service **SHALL** emit a structlog `warning` entry with `event="moneybird_webhook_auth_failed"` including the request's `request_id`. Verification: LogsQL `event:"moneybird_webhook_auth_failed"` in VictoriaLogs returns the expected row during the test run.

## AC-8: Timing comparison benchmark exists and documents constant-time property

- **WHEN** a pytest microbenchmark in `tests/test_meetings_webhook_auth.py` (or a sibling file) runs `_require_webhook_secret` 1000 times with wrong tokens of length 1, 16, and 64 characters AND measures per-call wall-clock time **THE** mean per-call difference across the three lengths **SHALL** be below 50 microseconds.
- The benchmark **SHALL** be marked `@pytest.mark.slow` so the CI fast path can skip it.
- The benchmark is documentation of the property, not a strict enforcement gate. A high-variance CI runner that trips the 50us threshold triggers investigation, not a test-suite failure (the marker allows `pytest -m "not slow"`).

## AC-9: Legitimate Caddy-forwarded request with correct Bearer returns 200

- **WHEN** a POST is made to `/api/bots/internal/webhook` with `X-Forwarded-For: <real-vexa-caller-ip>` AND `Authorization: Bearer <correct-vexa_webhook_secret>` AND a valid Vexa payload body **THE** service **SHALL** return HTTP 200 and process the meeting transcript as before.
- Verification: pytest using the existing FastAPI test client, with `vexa_webhook_secret` overridden via `Settings` and the Authorization header set to match. Must cover the happy path post-IP-bypass-removal.

## AC-10: Existing webhook callers keep working after coordinated deploy

- **WHEN** Vexa's `POST_MEETING_HOOKS` is updated to include `Authorization: Bearer <vexa_webhook_secret>` in the same deploy window as this SPEC's code changes **THE** first real post-meeting callback after deploy **SHALL** return 200 and the meeting **SHALL** appear with `status="processed"` in the portal.
- **WHEN** the Moneybird dashboard sends a legitimate webhook with the correct `webhook_token` in the payload AND `MONEYBIRD_WEBHOOK_TOKEN` is correctly configured in SOPS **THE** handler **SHALL** return 200 and subscription state **SHALL** transition as before.
- This AC is operational, not unit-testable -- it is the go/no-go check during the deploy window.

---

## AC-11: Every klai FastAPI service Dockerfile launches uvicorn with --proxy-headers

- **WHEN** a static grep across `klai-portal/backend/entrypoint.sh`, `klai-retrieval-api/Dockerfile`, `klai-knowledge-ingest/Dockerfile`, AND `klai-scribe/scribe-api/Dockerfile` for lines containing `uvicorn` runs **THE** result **SHALL** show `--proxy-headers` on every matching line (whether directly or by virtue of the line calling the shared wrapper from REQ-6 which injects the flag).
- **WHEN** the same grep runs for `uvicorn .* --host .* --port` lines that LACK `--proxy-headers` AND LACK the shared-wrapper invocation **THE** result **SHALL** be zero matches. Equivalent command:
  ```bash
  grep -rn 'uvicorn' klai-portal/backend/entrypoint.sh klai-retrieval-api/Dockerfile \
      klai-knowledge-ingest/Dockerfile klai-scribe/scribe-api/Dockerfile \
      | grep -v -- '--proxy-headers' | grep -v '<wrapper-name>' | wc -l
  # expected: 0
  ```
- Maps to REQ-1.1, REQ-6.2.

## AC-12: Shared uvicorn launch wrapper exists and fails closed

- **WHEN** the shared wrapper (per REQ-6.1 -- script, Make target, or base Dockerfile layer) is invoked WITHOUT `UVICORN_FORWARDED_ALLOW_IPS` set **THE** wrapper **SHALL** exit non-zero AND uvicorn **SHALL NOT** bind. Verification: run the wrapper in a test shell with the env var unset and assert a non-zero exit and no listening socket.
- **WHEN** the wrapper is invoked WITH `UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1` (the safe internal-service default) **THE** wrapper **SHALL** start uvicorn with `--proxy-headers --forwarded-allow-ips=127.0.0.1`. Verification: inspect the resulting `ps` line inside the container or the uvicorn startup log line.
- **WHEN** `.claude/rules/klai/lang/python.md` (or a sibling rules file) is inspected **THE** file **SHALL** contain a section documenting the wrapper, its env-var contract, and a reference to this SPEC.
- Maps to REQ-6.1, REQ-6.3, REQ-6.4.

## AC-13: retrieval-api rate-limit key ignores spoofed X-Forwarded-For from untrusted peers

- **WHEN** a POST is made to any rate-limited retrieval-api endpoint (e.g. `/retrieve`) from a simulated klai-net peer with TCP peer IP `172.18.0.42`, a valid `X-Internal-Secret`, AND a forged `X-Forwarded-For: 1.2.3.4` **THE** rate-limit key used for bucket identity **SHALL** be `retrieval:rl:internal:172.18.0.42`, NOT `retrieval:rl:internal:1.2.3.4`.
- **WHEN** the same request is inspected **THE** key **SHALL NOT** contain the string `1.2.3.4` anywhere. Verification: pytest using the FastAPI TestClient (which does NOT route through Caddy, so `request.client.host` is the test-client's own peer IP) with a forged `X-Forwarded-For` header, asserting that the rate-limit key produced by `_rate_limit_key(auth, request)` uses the peer, not the header.
- **WHEN** 601 such requests are sent in 60 seconds from the same TCP peer **THE** 601st request **SHALL** be rate-limited (HTTP 429 or the service's equivalent). **AND WHEN** the same 601 requests are sent with `X-Forwarded-For` values that all differ **THE** 601st request **SHALL** still be rate-limited -- the header does NOT split the bucket.
- Maps to REQ-1.5, REQ-5.6.

---

## Mapping AC -> REQ

| AC | REQ |
|---|---|
| AC-1 | REQ-2.1 |
| AC-2 | REQ-2.3 |
| AC-3 | REQ-2.2, REQ-5.1 |
| AC-4 | REQ-1.1, REQ-1.2, REQ-1.4 |
| AC-5 | REQ-3.1, REQ-3.2, REQ-5.2 |
| AC-6 | REQ-3.2, REQ-4.1 |
| AC-7 | REQ-4.2, REQ-4.3, REQ-5.3 |
| AC-8 | REQ-5.4 |
| AC-9 | REQ-5.5 |
| AC-10 | Non-functional backward-compatibility clause |
| AC-11 | REQ-1.1 (expanded), REQ-6.2 |
| AC-12 | REQ-6.1, REQ-6.3, REQ-6.4 |
| AC-13 | REQ-1.5, REQ-5.6 |
