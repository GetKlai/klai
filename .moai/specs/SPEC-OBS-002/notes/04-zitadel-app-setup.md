# Note 04 — Zitadel app setup (admin API path)

**Status:** ✅ Confirmed working pattern, 2026-05-05

The Zitadel Management API is reachable from any machine that has the
`ZITADEL_ADMIN_PAT` (klai-admin-sa, IAM_OWNER role). Pattern documented
in `.claude/rules/klai/platform/zitadel.md`.

## Working click-path (replay-able for M2)

### Pull PAT (only readable on core-01 by user `klai`)

```bash
PAT=$(ssh core-01 "grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env" | cut -d= -f2-)
```

### Create project

```bash
ZITADEL="https://auth.getklai.com"
ORG_ID="362757920133283846"   # klai-admin-sa's org

curl -sS -X POST "${ZITADEL}/management/v1/projects" \
  -H "Authorization: Bearer ${PAT}" \
  -H "X-Zitadel-Orgid: ${ORG_ID}" \
  -H "Content-Type: application/json" \
  -d '{"name":"klai-vmauth"}'
# → returns {"id":"<project_id>",...}
```

### Create OIDC app (auth-code + PKCE — recommended per Note 02)

```bash
PROJECT_ID="<from previous step>"

curl -sS -X POST "${ZITADEL}/management/v1/projects/${PROJECT_ID}/apps/oidc" \
  -H "Authorization: Bearer ${PAT}" \
  -H "X-Zitadel-Orgid: ${ORG_ID}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "vlogs-cli",
    "redirectUris": ["http://localhost:8765/callback"],
    "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
    "grantTypes": [
      "OIDC_GRANT_TYPE_AUTHORIZATION_CODE",
      "OIDC_GRANT_TYPE_REFRESH_TOKEN"
    ],
    "appType": "OIDC_APP_TYPE_NATIVE",
    "authMethodType": "OIDC_AUTH_METHOD_TYPE_NONE",
    "version": "OIDC_VERSION_1_0",
    "devMode": false,
    "accessTokenType": "OIDC_TOKEN_TYPE_JWT",
    "accessTokenRoleAssertion": false,
    "idTokenRoleAssertion": false,
    "idTokenUserinfoAssertion": false
  }'
# → returns {"clientId":"<numeric-string>","appId":"...",...}
```

`accessTokenType=JWT` is required so vmauth can validate signatures.
`authMethodType=NONE` means PKCE-only (no client secret), correct for
Native CLI apps.

### Cleanup (after PoC / when revoking)

```bash
curl -sS -X DELETE "${ZITADEL}/management/v1/projects/${PROJECT_ID}" \
  -H "Authorization: Bearer ${PAT}" \
  -H "X-Zitadel-Orgid: ${ORG_ID}"
# → 200 OK; cascades to all apps in the project
```

## Notes for M2

- The PAT call is rate-limited (default Zitadel limits, generous).
- `X-Zitadel-Orgid` is **required** for any operation that creates
  resources scoped to an org — without it, Zitadel falls back to the
  PAT's home org which may be wrong. See zitadel.md HIGH-rule.
- `/oidc` (read) vs `/oidc_config` (update) is a foot-gun documented in
  zitadel.md — for create we use `/apps/oidc` (POST). For update we
  use `/apps/{appId}/oidc_config` (PUT).
- Project ID + Client ID should be committed to `klai-infra` SOPS
  (`KLAI_VLOGS_OIDC_CLIENT_ID`, `KLAI_VLOGS_OIDC_PROJECT_ID`) so
  `klai-login` reads from a known location, not hardcoded.

## What we deleted post-M1

The `klai-vmauth-poc` project (id `371627391312723985`) was created
during Milestone 1 PoC and deleted at the end. No remnants. M2 will
create a fresh `klai-vmauth` project (production name, no `-poc`
suffix).
