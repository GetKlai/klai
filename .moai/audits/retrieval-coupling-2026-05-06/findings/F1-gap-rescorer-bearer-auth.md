# F1 — gap_rescorer authentiseert verkeerd → 401 silent

**Severity:** HIGH
**Status:** OPEN — needs verification

## Initial finding

In [`klai-portal/backend/app/services/gap_rescorer.py:96-98`](../../../klai-portal/backend/app/services/gap_rescorer.py#L96-L98):

```python
headers = {"X-Caller-Service": "portal-api", **get_trace_headers()}
if settings.internal_secret:
    headers["Authorization"] = f"Bearer {settings.internal_secret}"
```

retrieval-api `AuthMiddleware` ([`klai-retrieval-api/retrieval_api/middleware/auth.py:325-365`](../../../klai-retrieval-api/retrieval_api/middleware/auth.py#L325-L365)) treats elke `Authorization: Bearer <token>` als JWT. Geen fallback naar X-Internal-Secret-header pad bij decode-failure:

```python
if bearer_token is not None:
    payload, error = await _decode_jwt(bearer_token)
    if error is not None:
        return _unauthorized(error)        # ← 401, geen retry
elif internal_header is not None:
    ...
```

**Verwacht effect:** elke gap-rescore call → 401 `invalid_jwt_signature`. gap_rescorer's `if not resp.is_success: continue` ([line 114-119](../../../klai-portal/backend/app/services/gap_rescorer.py#L114-L119)) eet de fout, logt warning, kennis-gaps worden nooit auto-resolved.

## Wired-up confirm

`schedule_rescore` wordt aangeroepen vanuit:
- `klai-portal/backend/app/api/internal.py:506` (page-save)
- `klai-portal/backend/app/api/internal.py:743` (connector sync)

Dus de service draait wel.

## Open vragen voor verificatie

1. Komen er werkelijk 401's binnen op retrieval-api van portal-api caller? Query VictoriaLogs:
   ```
   service:retrieval-api AND auth_rejected AND reason:invalid_jwt_signature
   ```
   over de afgelopen 30 dagen, en check of de timing samenvalt met page-save / connector-sync events.
2. Is er ergens nog een mechanisme dat de `Authorization: Bearer` als shared-secret accepteert dat ik gemist heb? Bijv. een legacy-pad dat in test-fixtures wel werkt maar in productie niet?
3. Hebben de gaps vóór 2026-04-25 (commit `0c29e6c5`) ooit wél resolved? Query `portal_retrieval_gaps` kolom `resolved_at IS NOT NULL` — is dat na 2026-04-25 op nul gevallen?

## Voorgestelde fix (voor agent te valideren)

Eén regel:

```python
headers = {
    "X-Internal-Secret": settings.internal_secret,
    "X-Caller-Service": "portal-api",
    **get_trace_headers(),
}
```

Verwijder de `if settings.internal_secret:` guard — als secret leeg is, is gap_rescorer sowieso niet bedoeld om te draaien. Of: voeg test toe die de outbound header controleert tegen retrieval-api auth contract.

## Verification

**Status: CONFIRMED (mechanism) / PARTIALLY CONFIRMED (production impact)**

### Code-trace verification — CONFIRMED

`klai-retrieval-api/retrieval_api/middleware/auth.py:325-365` reads the request
exactly as the finding describes. There is **no fallback path** from the Bearer
arm to the internal-secret arm:

```python
internal_header = request.headers.get("x-internal-secret")
auth_header = request.headers.get("authorization", "")
bearer_token: str | None = None
if auth_header.lower().startswith("bearer "):
    bearer_token = auth_header[len("Bearer ") :].strip() or None

# REQ-1.3: prefer JWT path when both credentials are present.
if bearer_token is not None:
    payload, error = await _decode_jwt(bearer_token)
    if error is not None:
        return _unauthorized(error)             # ← 401, hard return
    ...
elif internal_header is not None:
    if not _constant_time_secret_match(...):
        return _unauthorized("invalid_internal_secret")
    ...
else:
    return _unauthorized("missing_credentials")
```

`_decode_jwt` itself fails-closed on a non-JWT input. Verified empirically
with the same `python-jose` version retrieval-api uses:

```
$ uv run python -c "from jose import jwt; jwt.get_unverified_header('some_random_secret_abc123')"
JWTError: Error decoding token headers.
```

A plain shared-secret string has no `.` separators (verified on prod:
`INTERNAL_SECRET_LEN=64`, `INTERNAL_DOTS=0` → 64-char hex token), so the very
first decode step throws `JWTError`, which the handler converts to
`return {}, "invalid_jwt_signature"` (auth.py:202-203). Result: every
gap_rescorer call lands on the JWT arm and gets 401 without ever reaching
the internal-secret comparison. **Mechanism confirmed.**

### Caller construction — CONFIRMED

`klai-portal/backend/app/services/gap_rescorer.py:96-98` builds the header
dict exactly as the finding shows. Line 98 is unconditional once
`settings.internal_secret` is truthy (which the prod validator enforces —
`config.py:471-485`).

### Cross-caller asymmetry — STRONG SMOKING GUN

The fix-commit `0377f550` (PR #311, 2026-05-05, "fix: caller-service header
on every /retrieve caller") touched four callers in one PR. Three got
`X-Internal-Secret`; gap_rescorer got `Authorization: Bearer`:

| Caller (commit 0377f550) | Header used |
|---|---|
| `klai-portal/.../partner_chat.py:136` | `X-Internal-Secret: retrieval_secret` |
| `klai-focus/.../retrieval_client.py` | `X-Internal-Secret: secret` |
| `deploy/litellm/klai_knowledge.py:169` | `X-Internal-Secret: RETRIEVAL_INTERNAL_SECRET` |
| `klai-portal/.../gap_rescorer.py:97-98` | `Authorization: Bearer {settings.internal_secret}` |

The other three callers ALSO use the dedicated `retrieval_api_internal_secret`
setting, not the generic `internal_secret`. gap_rescorer uses the wrong
secret name AND the wrong header. Two stacked bugs.

The Bearer-as-shared-secret pattern was first introduced in commit
`a3311246` (2026-03-27, SPEC-KB-015: "auto-close gaps when retrieval
confidence recovers") — predates SPEC-SEC-010. Git diff:

```
+    headers = {"Authorization": f"Bearer {settings.internal_secret}"} if settings.internal_secret else {}
```

There is no commit in `klai-retrieval-api/retrieval_api/middleware/auth.py`
(or any predecessor under `klai-retrieve-api/`) that ever accepted Bearer
as a shared secret. The receiver was JWT-only from inception.

### Test coverage — INADEQUATE (false-confidence)

`klai-portal/backend/tests/test_gap_rescorer.py` exists with 7 tests, all
green. The relevant assertion (line 73-76):

```python
post_headers = mock_client.post.call_args.kwargs["headers"]
assert post_headers.get("X-Caller-Service") == "portal-api", (...)
```

The test only checks the `X-Caller-Service` header — never the auth header.
The httpx client is mocked with `AsyncMock` returning `is_success=True`, so
the test never validates the auth shape against a real or fake retrieval-api
auth contract. **The test would still pass even if gap_rescorer sent
no auth header at all.**

### Production evidence — PARTIALLY CONFIRMED (different shape than predicted)

VictoriaLogs (30d retention, queried 2026-04-22 → 2026-05-06):

| Reason | Count over 14 days |
|---|---|
| `invalid_internal_secret` | 64 |
| `invalid_jwt_audience` | 13 |
| **`invalid_jwt_signature`** | **0** |

Zero `invalid_jwt_signature` rejections. If gap_rescorer were calling
retrieval-api regularly, this counter should be elevated.

DB check on prod (`core-01`):

```
SELECT COUNT(*) AS total, MIN(occurred_at), MAX(occurred_at)
FROM portal_retrieval_gaps;
 total | min | max
-------+-----+-----
     0 |     |
```

The `portal_retrieval_gaps` table is **completely empty**. The schema
exists (`information_schema.tables` returned 1), `KNOWLEDGE_RETRIEVE_URL` is
set (`http://retrieval-api:8040`), and `schedule_rescore` is wired in
(`internal.py:506` page-save, `:743` connector sync). But no gap rows are
being recorded by `/internal/v1/gap-events` from the LiteLLM hook — which
means rescore loop iterations always exit at the "no open gaps" branch
(`gap_rescorer.py:86-88`) before constructing any HTTP request. The
auth bug therefore never fires in steady state.

This matches the SPEC-SEC-IDENTITY-ASSERT-001 hotfix history: PR #311 was
prompted by "0 KB injection lines in the last 6h, hundreds of retrieval
failed (400) warnings" — the LiteLLM `/retrieve` chat path was silently
failing for 7 days, so no gap classification ran, so no gap events were
emitted, so `portal_retrieval_gaps` stayed empty.

**Net effect:** the bug is real and 100% reproducible (a single test
hitting retrieval-api with the actual headers gap_rescorer sends would
return 401), but it is **dormant** in production because the upstream
gap-emit pipeline isn't producing inputs. It will start firing the moment
either:
1. The LiteLLM hook starts emitting gap events again (possibly already
   happening since PR #311 fix landed 2026-05-05, but no new gap rows
   yet at the time of this audit on 2026-05-06), OR
2. A different code path inserts into `portal_retrieval_gaps`.

The verifications-by-falsification I attempted:
- "Maybe `_decode_jwt` short-circuits when audience is wrong before signature?" — No, `get_unverified_header` runs first and dies first on a non-JWT string.
- "Maybe there's a legacy fallback path that accepts Bearer-as-shared-secret?" — No: `git log -p` over the entire history of `klai-retrieval-api/retrieval_api/middleware/auth.py` (and prior `klai-retrieve-api/` paths) shows the JWT path has always been signature-verified-only; no shared-secret-as-Bearer ever existed.
- "Maybe the test_gap_rescorer test exercises real auth?" — No, it asserts on a mocked httpx response and never compares the Authorization header to anything.

## Recommended fix

**Status: CONFIRMED**

Apply the one-line fix exactly as proposed, with one tweak — use the dedicated
`retrieval_api_internal_secret` (matching `partner_chat.py`) so gap_rescorer
crosses the same trust boundary the rest of the portal-api → retrieval-api
traffic does:

```python
# klai-portal/backend/app/services/gap_rescorer.py:96-98

retrieval_secret = settings.retrieval_api_internal_secret or settings.internal_secret
headers = {
    "X-Internal-Secret": retrieval_secret,
    "X-Caller-Service": "portal-api",
    **get_trace_headers(),
}
```

Drop the `if settings.internal_secret:` guard — the prod validator already
enforces non-empty internal_secret (config.py:471-485) and
retrieval_api_internal_secret (config.py:536-549). If the secret is empty
in dev, gap_rescorer should fail-loud rather than send an unauthenticated
request that 401s anyway.

Mirror the existing fallback pattern used in `partner_chat.py:130`:
`settings.retrieval_api_internal_secret or settings.internal_secret` — preserves
backward-compat for any environment where only the legacy `internal_secret`
is set, while preferring the dedicated secret when configured.

### Test addition (mandatory before merge)

Extend `tests/test_gap_rescorer.py::test_rescore_marks_resolved_when_no_longer_gap`:

```python
post_headers = mock_client.post.call_args.kwargs["headers"]
assert post_headers.get("X-Caller-Service") == "portal-api"
assert post_headers.get("X-Internal-Secret") == "test-secret", (
    "X-Internal-Secret missing or wrong — retrieval-api AuthMiddleware "
    "rejects Authorization: Bearer with invalid_jwt_signature 401. "
    "See finding F1, audits/retrieval-coupling-2026-05-06/."
)
assert "Authorization" not in post_headers, (
    "Authorization: Bearer is treated as JWT-only by retrieval-api. "
    "Use X-Internal-Secret instead."
)
```

The negative assertion (`"Authorization" not in post_headers`) is the
regression-guard that pins the fix.

### Cross-repo audit (mechanical, not narrative)

Per `.claude/rules/klai/pitfalls/process-rules.md` →
`retrieve-caller-service-header-mismatch`, run the same
allowlist sweep across every caller of `/retrieve`. As of 2026-05-06 four
known callers exist (partner_chat, gap_rescorer, litellm hook, focus
research-api retrieval_client). Three are correct; gap_rescorer is the
outlier. Future callers should land with header-shape regression tests
identical to the one above.

## Risk if not fixed

**Status: CONFIRMED — currently dormant, becomes acute on first gap event**

**Today (dormant):** zero production impact because `portal_retrieval_gaps`
is empty. The bug consumes no requests and emits no errors.

**The moment the upstream pipeline recovers:** every page-save and connector-
sync will fire `schedule_rescore` → fresh DB session sees gap rows → 50
queries/trigger × 401 each. Side effects:
- Knowledge gaps that should auto-resolve will stay open indefinitely. The
  product feature ("close gaps when retrieval confidence recovers",
  SPEC-KB-015) is silently non-functional.
- `gap_rescorer.py:114-119` swallows `resp.is_success == False` as a
  warning log and `continue` — no exception, no alert, no metric increment.
  Per the file pattern this matches the same fail-open class as
  `retrieve-caller-service-header-mismatch` (process-rules.md) — silent
  degradation on a feature the user thinks they have.
- Retrieval-api's `auth_rejected_total{reason="invalid_jwt_signature"}`
  Prometheus counter starts ticking, but no Grafana alert currently fires
  on it. (The PR #311 alert only watches `litellm KB retrieval failed`
  log lines, not retrieval-api's auth_rejected counter.)
- `_source_ip` rate-limit bucket for portal-api's container IP fills with
  401-rejected requests. Up to 600 rpm before hitting `_rate_limited`
  (settings.rate_limit_rpm); rescore loops cap at 50 queries × N orgs.
  Probably under the limit but adjacent legitimate traffic from portal-api
  shares the same `retrieval:rl:internal:<source_ip>` key and competes for
  the same budget.

**Detection time without the fix:** indefinite. The DB is empty so a metric
on `resolved_count` would show 0 (correctly, given 0 inputs). Only a
side-by-side comparison of "rows inserted into portal_retrieval_gaps" vs
"rows where resolved_at IS NOT NULL" would surface the gap. Today neither
counter exists in any dashboard.

**Severity:** HIGH (matches initial finding). Reasoning:
- Mechanism is confirmed and 100% reproducible.
- Production evidence is dormant only because of a separate upstream issue;
  the fix for that upstream issue (PR #311, 2026-05-05) has already landed.
- First successful end-to-end gap event recorded after PR #311 will trigger
  the silent-degrade. Fix is one line; cost of leaving it in is full
  feature regression.

Recommended action: apply the one-line fix as part of this audit's
remediation batch, NOT as a separate SPEC. The change is too small to merit
its own SPEC and the rationale is already captured in this finding +
pitfalls/process-rules.md.
