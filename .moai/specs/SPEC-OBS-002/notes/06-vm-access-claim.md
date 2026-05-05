# Note 06 — vmauth requires `vm_access` claim (M2 finding)

**Date:** 2026-05-05
**Status:** ✅ Resolved with Zitadel Action

## What we found

vmauth v1.142.0 returns 401 with reason `invalid_auth_token` for any JWT
that lacks a `vm_access` claim — regardless of whether `match_claims`
is configured. This is hardcoded behaviour:

```
cannot parse jwt token: cannot parse token body: missing `vm_access` claim
```

Earlier docs research suggested `vm_access` was optional ("only required
for templating"). It is not. Even an empty `vm_access: {}` is enough,
but the claim **must exist**.

## Why we found it now (and not in M1)

M1 PoC used `client_credentials` grant which failed for unrelated reasons
(`invalid_client`), so we never minted a Zitadel JWT to feed vmauth. M2
acquired a real auth-code-flow JWT, fed it to vmauth, got the explicit
error message after enabling `-logInvalidAuthTokens=true`.

## Solution

Zitadel **Action** (Complement Token Flow → Pre Access Token Creation
trigger) injects `vm_access: {}` into every access token minted for the
`klai-vmauth` project's `vlogs-cli` app. Once bound, every JWT acquired
via auth-code+PKCE for our app will have the claim, vmauth will accept
it, and the rest of the validation (signature, exp, iss, aud match)
proceeds normally.

## Setup

### Action created via Management API (done)

```bash
curl -X POST "https://auth.getklai.com/management/v1/actions" \
  -H "Authorization: Bearer ${ZITADEL_ADMIN_PAT}" \
  -H "X-Zitadel-Orgid: 362757920133283846" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "vmauth-vm-access-claim",
    "script": "function vmauth(ctx, api) { api.v1.claims.setClaim(\"vm_access\", {}); }",
    "timeout": "10s",
    "allowedToFail": false
  }'
# Returns: {"id": "371632974619213841"}
```

### Trigger binding — via Zitadel UI (one-time, ~30s)

The Management API endpoint for `SetTriggerActions` resisted curl
discovery (every URL shape returned 404 or "FlowType missing"). The
Zitadel admin UI does it in three clicks:

1. https://auth.getklai.com/ui/console → log in as admin.
2. Default Org → **Actions** (left sidebar).
3. **Flows** tab → select **Complement Token** flow type.
4. Find the **Pre Access Token Creation** trigger row.
5. Click "+" or "Add" → select `vmauth-vm-access-claim` action → Save.

After binding, **every** JWT minted by Zitadel (across all apps) gets
`vm_access: {}`. This is acceptable for our use because the claim is
empty — it doesn't grant anything; it merely satisfies vmauth's parser.

## Verification

After binding, re-acquire a JWT via auth-code flow and confirm:

```bash
echo -n "$JWT" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

The decoded payload should include `"vm_access": {}` alongside the
existing `iss`, `sub`, `aud`, `exp`, `iat`, `nbf`, `client_id`, `jti`
fields.

Then:

```bash
ssh core-01 "docker exec klai-core-caddy-1 wget -qO- --server-response \
  --header='Authorization: Bearer $JWT' \
  'http://vmauth:8427/select/logsql/query?query=service:caddy&start=now-1m&end=now&limit=1' 2>&1 | head -3"
```

Expected: `HTTP/1.1 200 OK` + log data (or empty result set, both
acceptable — the auth path passed).

## Implication for SPEC

- M2 architecture validated end-to-end.
- klai-login (M3) inherits `vm_access` automatically via the Action —
  no laptop-side change needed.
- Other Klai services that consume Zitadel JWTs (portal-api, retrieval,
  etc.) now also get `vm_access: {}` in their tokens. Empty payload —
  no security or functional impact, just an extra noop claim.

## Cleanup if we decide against this approach later

```bash
# Unbind via UI (Actions → Flows → Pre Access Token Creation → remove).
# Or delete the action entirely:
curl -X DELETE "https://auth.getklai.com/management/v1/actions/371632974619213841" \
  -H "Authorization: Bearer ${ZITADEL_ADMIN_PAT}" \
  -H "X-Zitadel-Orgid: 362757920133283846"
```
