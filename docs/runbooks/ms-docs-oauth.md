# Runbook: Microsoft 365 OAuth — app registration & rotation

> Setup and operational runbook for the `ms_docs` connector (SPEC-KB-MS-DOCS-001).
> Covers Azure AD app registration, SOPS secret provisioning, verification and rotation.

## Ownership

The Azure AD app is registered in the **Klai-owned M365 tenant** (same ownership
model as `ZITADEL_IDP_MICROSOFT_ID` for social login). Klai team manages
credentials; klai customers grant tenant-wide admin-consent once when they
enable the connector.

**Credentials (SOPS):**
- `MS_DOCS_CLIENT_ID` — Application (client) ID from the Azure portal
- `MS_DOCS_CLIENT_SECRET` — Client secret value (not secret ID)
- `MS_DOCS_TENANT_ID` — `common` (multi-tenant; do not change without a SPEC)

## First-time app registration (Klai-owned tenant)

Prereq: signed in to [portal.azure.com](https://portal.azure.com) as an admin
of the Klai M365 tenant.

### 1. Create the App registration

1. **Entra ID → App registrations → New registration**
2. **Name**: `Klai Knowledge — Office 365 Connector`
3. **Supported account types**: **Accounts in any organizational directory (Any Microsoft Entra ID tenant — Multitenant)**
4. **Redirect URI**:
   - Type: **Web**
   - URI: `https://my.getklai.com/api/oauth/ms_docs/callback`
5. Click **Register**. Copy the **Application (client) ID** — this is `MS_DOCS_CLIENT_ID`.

### 2. Add API permissions (delegated, not application)

**API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add:

- `offline_access` — required for refresh_token
- `User.Read` — identity (populates `sender_email`)
- `Files.Read.All` — OneDrive + SharePoint files the user can access
- `Sites.Read.All` — SharePoint site enumeration / site-URL resolution

Do **not** add any `Application` permissions — we use delegated-only (see D3 in the SPEC).

**Grant admin consent for Klai tenant** (for dev/test). Customers will grant
consent for their own tenants the first time their admin clicks "Connect Office 365".

### 3. Create a client secret

**Certificates & secrets → Client secrets → New client secret**

- Description: `Klai connector (rotated YYYY-MM)`
- Expires: **6 months** (see Rotation below)

Copy the **Value** immediately — this is `MS_DOCS_CLIENT_SECRET`. The portal
only shows this once.

### 4. Publisher verification (optional, recommended)

If you want to ship out of beta: complete [publisher verification](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview)
in the tenant. Unverified apps trigger a warning banner during user consent.

## SOPS provisioning

Add the three values to the core-01 encrypted env file:

```bash
cd klai-infra/core-01
sops -d .env.sops > .env.decrypted
# Append / update:
#   MS_DOCS_CLIENT_ID=<application-client-id>
#   MS_DOCS_CLIENT_SECRET=<client-secret-value>
#   MS_DOCS_TENANT_ID=common
sops -e .env.decrypted > .env.sops.new && mv .env.sops.new .env.sops
rm .env.decrypted
git diff .env.sops   # verify only the ciphertext blocks changed
git add .env.sops && git commit -m "chore(infra): add MS_DOCS_* for SPEC-KB-MS-DOCS-001"
git push
```

GitHub Action auto-syncs to `/opt/klai/.env`. Verify after sync:

```bash
ssh core-01 "sudo grep '^MS_DOCS_' /opt/klai/.env | sed 's/=.*/=***/'"
# Expected: MS_DOCS_CLIENT_ID=*** / MS_DOCS_CLIENT_SECRET=*** / MS_DOCS_TENANT_ID=***
```

## Container rollout

```bash
ssh core-01 "cd /opt/klai && docker compose up -d portal-api klai-connector"
# Wait ~10s, then verify:
ssh core-01 "docker logs --tail 30 klai-core-portal-api-1 2>&1 | grep -i ms_docs || true"
ssh core-01 "docker logs --tail 30 klai-core-klai-connector-1 2>&1 | grep -i ms_docs"
# Connector should log either: "ms_docs adapter registered" (implicit)
# or: "ms_docs adapter not registered — MS_DOCS_CLIENT_ID unset" (if env didn't propagate)
```

If the warning fires, check `docker compose config klai-connector | grep MS_DOCS_` —
the env var must reach the container.

## End-to-end verification

1. Log in to [my.getklai.com](https://my.getklai.com) as a user in the Klai tenant.
2. Go to a knowledge base → **Add connector** → **Office 365**.
3. Leave `SharePoint site URL` empty (personal OneDrive), click **Connect with Microsoft**.
4. Complete the Microsoft consent page. On first use per-tenant an admin must
   click through tenant-wide consent once.
5. Back in the portal: status becomes **Connected**.
6. Trigger sync → check VictoriaLogs:
   ```
   service:klai-connector AND connector_type:ms_docs
   ```
   Expect `Listing MS drive items` entries with positive `item_count`, no
   bearer tokens in the output.
7. In Qdrant, verify a chunk has `source_url` starting with
   `https://{tenant}.sharepoint.com/` and non-empty `sender_email`.
8. Edit a document in OneDrive → wait for the next scheduled sync → only that
   document should be re-ingested (incremental delta). Verify via
   `connector.sync_runs.cursor_state`:
   ```sql
   SELECT cursor_state FROM connector.sync_runs
   WHERE connector_id = '<uuid>' ORDER BY created_at DESC LIMIT 2;
   ```
   Two different `delta_link` values.

## Rotation

Microsoft client-secret has configurable expiry. Rotate **every 6 months** even
if not expired, per `runbooks/credential-rotation.md` cadence.

```
1. Azure Portal → Certificates & secrets → New client secret
2. Copy the new Value
3. Update SOPS: MS_DOCS_CLIENT_SECRET=<new-value>
4. Push; GitHub Action syncs /opt/klai/.env
5. ssh core-01 "docker compose up -d portal-api klai-connector"
6. Verify via end-to-end step 6 — new bearer tokens flowing
7. Delete the OLD secret in Azure AD (Certificates & secrets → Delete)
```

**Refresh-token rotation** is handled automatically by
[`OAuthAdapterBase.ensure_token`](../../klai-connector/app/adapters/oauth_base.py):
Microsoft rotates refresh_tokens on each refresh; the adapter writebacks the new
one to portal via `PortalClient.update_credentials(refresh_token=...)` (SPEC R9).
No operator action needed for RT rotation — only client-secret rotation.

## Common issues

| Symptom | Likely cause | Action |
|---|---|---|
| `Connect Office 365` button missing in wizard | `MS_DOCS_CLIENT_ID` unset | Verify via `docker exec portal-api printenv MS_DOCS_CLIENT_ID` |
| 403 on `/sites/{host}:/{path}` resolution | Admin consent missing for `Sites.Read.All` | Ask customer admin to grant tenant-wide consent once |
| 404 on site resolution | User typed wrong site URL | Surface clean error in portal; no fix needed server-side |
| 410 Gone on delta call | deltaLink expired (rare, > 30d inactive) | Adapter handles this — catches 410 and resets cursor to trigger full re-sync next run |
| Sync returns 401 after weeks of working | Refresh token got invalidated (user changed password, revoked consent) | User clicks "Reconnect Microsoft" on the edit-connector page |

## References

- [SPEC-KB-MS-DOCS-001](../../.moai/specs/SPEC-KB-MS-DOCS-001/spec.md)
- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [driveItem: delta](https://learn.microsoft.com/en-us/graph/api/driveitem-delta)
- [Refresh tokens in Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)
- Klai runbooks: [credential-rotation.md](./credential-rotation.md), [platform-recovery.md](./platform-recovery.md)
