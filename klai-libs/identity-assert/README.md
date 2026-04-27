# klai-identity-assert

Shared identity-assertion helper for Klai service-to-service calls.
Implements [SPEC-SEC-IDENTITY-ASSERT-001](../../.moai/specs/SPEC-SEC-IDENTITY-ASSERT-001/spec.md)
REQ-7.

## Why this exists

Every Klai service-to-service call that carries a tenant or user identity
claim must verify that claim against a source of truth before acting on
it. The shared `INTERNAL_SECRET` proves *network* identity (the caller is
one of our services); it does **not** prove *tenant* identity.

This library is the one and only implementation of "ask portal-api whether
this identity claim is real". Every Python consumer
(`klai-knowledge-mcp`, `klai-scribe`, `klai-retrieval-api`, future
additions) imports it. Services do not re-implement the contract.

## When to call

Call `IdentityAsserter.verify` immediately before any operation that:

1. Reads, writes, or filters data on behalf of a specific user or org, AND
2. Receives the user/org identity from a service-to-service caller (i.e.
   not directly from a Zitadel JWT validated in this service's own
   middleware).

If your handler authenticates via Zitadel JWT directly and uses the
JWT's `sub` / `resourceowner` claims, you do **not** need this library —
your auth middleware already verified the identity.

## Quickstart

```python
from klai_identity_assert import IdentityAsserter

asserter = IdentityAsserter(
    portal_base_url=settings.portal_base_url,        # e.g. "http://portal-api:8000"
    internal_secret=settings.internal_secret,        # the shared INTERNAL_SECRET
)

result = await asserter.verify(
    caller_service="scribe",            # one of KNOWN_CALLER_SERVICES
    claimed_user_id=user_id,
    claimed_org_id=org_id,
    bearer_jwt=jwt_or_none,             # forward end-user JWT when available
    request_headers=request.headers,    # propagates X-Request-ID for tracing
)

if not result.verified:
    # Refuse the upstream operation. The reason MUST stay in logs only —
    # echoing it to the end-user client leaks information (REQ-2.2).
    logger.warning("identity_assertion_failed", reason=result.reason)
    raise HTTPException(403, detail="identity_assertion_failed")

# Proceed with result.user_id / result.org_id (the canonical resolved tuple).
```

## Contract

### `caller_service` allowlist

Mirrors portal-api REQ-1.2. Currently:

- `knowledge-mcp`
- `scribe`
- `retrieval-api`
- `connector`
- `mailer`

Adding a new service requires a synchronised change in
`klai-portal/backend/app/api/internal.py` and this library's
`KNOWN_CALLER_SERVICES`. If only one side is updated, calls fail closed
with `library_misconfigured` (consumer side) or `unknown_caller_service`
(portal side).

### `bearer_jwt` semantics

| Caller passes | Portal evidence | When to use |
|---|---|---|
| Zitadel JWT (active) | `"jwt"` | Strongest assertion — caller forwarded the end-user JWT |
| `None` | `"membership"` | Fallback — portal looks up membership by `(user_id, org_id)` |
| Zitadel JWT (expired/invalid) | (deny) | Portal returns `invalid_jwt` deny — does NOT fall through to membership |

The `None` fallback is narrower (it only catches "this user belongs to
this org" — not "this caller is acting *as* this user"). Strong identity
binding requires forwarding the user's JWT.

### Reason codes

Stable codes returned in `VerifyResult.reason` on deny:

| Code | Source | Meaning |
|---|---|---|
| `unknown_caller_service` | portal | `caller_service` not in allowlist |
| `invalid_jwt` | portal | JWT signature/audience/exp failure |
| `jwt_identity_mismatch` | portal | JWT sub or resourceowner ≠ claimed tuple |
| `no_membership` | portal | Claimed user has no active membership in claimed org |
| `cache_unavailable` | portal | Redis-backed verifier cache unreachable (HTTP 503) |
| `portal_unreachable` | library | Network error / 5xx / malformed body / unrecognised reason |
| `library_misconfigured` | library | `caller_service` not in `KNOWN_CALLER_SERVICES` (caller bug) |

### Caching

- **Library side** (this package): per-process LRU, 60 s TTL, in-memory
  only. Verified results are cached; denials never are. Privacy-bound:
  the cache is per-consumer-process. See [REQ-7.2 + research §2.4](../../.moai/specs/SPEC-SEC-IDENTITY-ASSERT-001/research.md).
- **Portal side**: Redis-backed cache, 60 s TTL, keyed on
  `(caller_service, claimed_user_id, claimed_org_id, evidence)`.

The two caches are independent. A 60 s TTL on both sides means worst-case
revocation propagation is bounded at ~60 s, which is shorter than the
Zitadel JWT `exp` for any active user session.

### Failure mode — fail closed

The library MUST refuse operations under degraded conditions:

- Network error against portal → `portal_unreachable`
- Portal HTTP 5xx → `portal_unreachable`
- Malformed JSON body → `portal_unreachable`
- Reason code not in the documented list → `portal_unreachable`
- Caller passed an unknown `caller_service` → `library_misconfigured`

This is the deliberate inverse of the SPEC-SEC-005 rate limiter (which
fails open). An auth-class control fails closed. See [SPEC §11
"Risks & Mitigations"](../../.moai/specs/SPEC-SEC-IDENTITY-ASSERT-001/spec.md#risks--mitigations).

## Migration: before / after

### Before — caller-asserted identity (REJECTED PATTERN)

```python
# klai-scribe — body.org_id is trusted on faith
async def ingest_to_kb(body: IngestToKBRequest, user_id: str = Depends(jwt_user)):
    # ❌ body.org_id is whatever the caller chose to send
    await ingest_scribe_transcript(org_id=body.org_id, user_id=user_id)
```

### After — verified identity

```python
# klai-scribe — org_id resolved from JWT's resourceowner, then verified
async def ingest_to_kb(
    request: Request,
    body: IngestToKBRequest,
    user_id: str = Depends(jwt_user),
    bearer_jwt: str = Depends(bearer_token_str),
):
    org_id = jwt_payload.resourceowner  # canonical org from JWT

    result = await asserter.verify(
        caller_service="scribe",
        claimed_user_id=user_id,
        claimed_org_id=org_id,
        bearer_jwt=bearer_jwt,
        request_headers=request.headers,
    )
    if not result.verified:
        raise HTTPException(403, detail="identity_assertion_failed")

    await ingest_scribe_transcript(org_id=result.org_id, user_id=result.user_id)
```

## Telemetry

Every `verify` call emits one structlog event with stable key
`event="identity_assert_call"` and fields:

- `caller_service`
- `claimed_user_id_hash` (SHA-256 prefix; never the raw UUID)
- `claimed_org_id`
- `verified` (bool)
- `cached` (bool)
- `latency_ms`
- `evidence` (on allow)
- `reason` (on deny)

Allow events log at `info` level. Deny events log at `warning`. Combined
with portal-side `identity_verify_decision` events sharing the same
`X-Request-ID`, one VictoriaLogs LogsQL query traces the full chain:

```
event:"identity_assert_call" AND verified:false  # all denials, all callers
event:"identity_verify_decision" AND verified:false  # same, portal side
```

## Adding a new consumer

1. Add the service name to `KNOWN_CALLER_SERVICES` here AND to portal-api
   `/internal/identity/verify` allowlist (`klai-portal/backend/app/api/internal.py`).
2. Add `klai-identity-assert` as an editable install in your service's
   `pyproject.toml`:

   ```toml
   dependencies = [
       "klai-identity-assert @ file:///${PROJECT_ROOT}/../klai-libs/identity-assert",
       ...
   ]
   ```

   Mirror the `klai-image-storage` pattern that knowledge-ingest /
   connector use today.
3. Construct one `IdentityAsserter` per process (lifespan startup).
4. Replace any caller-asserted identity reads with `verify` results.
5. Add a regression test that asserts the call refuses upstream
   operations on `result.verified is False`.

## Versioning

Library version follows the SPEC version. v0.1.0 ships with the first
landing of REQ-1 + REQ-7 (Phase A of SPEC-SEC-IDENTITY-ASSERT-001).
Subsequent SPEC requirements (REQ-2 / REQ-3 / REQ-4 / REQ-6) consume
this library without changing it.
