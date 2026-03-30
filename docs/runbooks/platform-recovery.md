# Platform Recovery Runbooks

> Step-by-step emergency procedures. Use these when something is already broken.
> For prevention, see `pitfalls/platform.md`.

---

## zitadel-login-v2-recovery

**Situation:** Portal login is broken AND Zitadel console (`auth.getklai.com/ui/console`) redirects to the broken portal login — chicken-and-egg deadlock.

### Step 1 — Break the deadlock

Delete the Login V2 row directly in PostgreSQL. Takes effect immediately — no Zitadel restart needed.

```bash
POSTGRES=$(docker ps --format '{{.Names}}' | grep -i postgres | head -1)
docker exec $POSTGRES psql -U zitadel -d zitadel -c \
  "DELETE FROM projections.instance_features5
   WHERE instance_id = '362757920133218310' AND key = 'login_v2';"
```

`auth.getklai.com/ui/console` now uses Zitadel's built-in login.

### Step 2 — Fix the underlying portal issue

Fix whatever caused portal login to break (missing env var, crashed container, etc.).

### Step 3 — Re-enable Login V2

Write to a file to avoid shell quoting problems:

```bash
cat > /tmp/fix_login_v2.sql << 'EOF'
UPDATE projections.instance_features5
SET value = '{"base_uri": {"Host": "getklai.getklai.com", "Path": "", "User": null, "Opaque": "", "Scheme": "https", "RawPath": "", "Fragment": "", "OmitHost": false, "RawQuery": "", "ForceQuery": false, "RawFragment": ""}, "required": true}'::jsonb,
    change_date = NOW(),
    sequence = 5
WHERE instance_id = '362757920133218310' AND key = 'login_v2';
EOF
POSTGRES=$(docker ps --format '{{.Names}}' | grep -i postgres | head -1)
docker cp /tmp/fix_login_v2.sql $POSTGRES:/tmp/fix_login_v2.sql
docker exec $POSTGRES psql -U zitadel -d zitadel -f /tmp/fix_login_v2.sql
```

**Critical:** The value uses Go's `url.URL` struct serialization — do not abbreviate or change the structure. If you get this wrong, the projection row is written with `value = null` and Login V2 has no redirect target.

**Zitadel instance constants (core-01, do not guess):**

| Name | Value |
|---|---|
| Instance ID | `362757920133218310` |
| Feature aggregate creator | `362760545968848902` |
| portal-api machine user ID | `362780577813757958` |

---

## zitadel-pat-rotation

**Situation:** `create_session failed 401: Errors.Token.Invalid (AUTH-7fs1e)` in portal-api logs. All users get login errors.

### Step 1 — Generate a new PAT

Go to `https://auth.getklai.com/ui/console` (if Login V2 blocks you, see [#zitadel-login-v2-recovery](#zitadel-login-v2-recovery) above).

Navigate to **Users** → **Service Accounts** tab → **Portal API** → **Personal Access Tokens** → **+ New** — copy the token value (shown once only).

### Step 2 — Apply to running container

```bash
# Use sed to update the var in /opt/klai/.env
sed -i 's|^PORTAL_API_ZITADEL_PAT=.*|PORTAL_API_ZITADEL_PAT=<new-token>|' /opt/klai/.env
cd /opt/klai && docker compose up -d portal-api   # must be up -d, not restart
docker exec klai-core-portal-api-1 env | grep PORTAL_API_ZITADEL_PAT  # verify
```

### Step 3 — Verify the new PAT works

```bash
ssh core-01 "curl -s https://auth.getklai.com/v2/sessions \
  -H 'Authorization: Bearer <new-token>' \
  -H 'Content-Type: application/json' \
  -d '{\"checks\":{\"user\":{\"loginName\":\"test\"},\"password\":{\"password\":\"test\"}}}'"
# Expected: {"code":5, "message":"User could not be found"} — not 401
```

### Step 4 — Update .env.sops

Run from `/tmp` to avoid `.sops.yaml` path mismatch:

```bash
cd /tmp
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --decrypt --input-type dotenv --output-type dotenv \
  ~/Server/projects/klai/klai-infra/core-01/.env.sops > klai-env-plain

sed -i '' 's|^PORTAL_API_ZITADEL_PAT=.*|PORTAL_API_ZITADEL_PAT=<new-token>|' klai-env-plain

SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --encrypt --input-type dotenv --output-type dotenv \
  --age age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur,age15ztzw9vnngkdnw0pg5tn8upplglvhzkep23sm5zu86res5lcmv7syw5m4v \
  /tmp/klai-env-plain > ~/Server/projects/klai/klai-infra/core-01/.env.sops

rm /tmp/klai-env-plain  # never leave plaintext lying around

cd ~/Server/projects/klai/klai-infra && git add core-01/.env.sops && git commit -m "Rotate PORTAL_API_ZITADEL_PAT"
```

---

## portal-api-deploy-outage-recovery

**Situation:** `docker compose up -d portal-api` crashed — new required fields in `config.py` have no value in `.env`. All auth is broken (Login V2 routes Zitadel's own login through portal-api).

### Step 1 — Break the auth deadlock

If Login V2 is blocking the Zitadel console, run the Login V2 DELETE from [#zitadel-login-v2-recovery](#zitadel-login-v2-recovery) first.

### Step 2 — Get the missing secrets

| Env var | Config field | How to generate |
|---|---|---|
| `PORTAL_API_ZITADEL_PAT` | `zitadel_pat` | Zitadel console → Users → Service Accounts → Portal API → Personal Access Tokens → + New |
| `PORTAL_API_PORTAL_SECRETS_KEY` | `portal_secrets_key` | `openssl rand -hex 32` (must be 64 hex chars = 32 bytes) |
| `PORTAL_API_SSO_COOKIE_KEY` | `sso_cookie_key` | `python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

### Step 3 — Add to /opt/klai/.env

```bash
echo 'PORTAL_API_ZITADEL_PAT=<token>' >> /opt/klai/.env
echo 'PORTAL_API_PORTAL_SECRETS_KEY=<hex32>' >> /opt/klai/.env
echo 'PORTAL_API_SSO_COOKIE_KEY=<fernet-key>' >> /opt/klai/.env
```

Use single quotes. Never use double quotes — `$` in values gets interpolated and truncates the secret.

### Step 4 — Pre-flight check, then deploy

```bash
ssh core-01 "cd /opt/klai && docker compose config portal-api | grep -A 80 'environment:'"
# Verify all three new vars are non-empty in the output, then:
ssh core-01 "cd /opt/klai && docker compose up -d portal-api"
docker logs --tail 20 klai-core-portal-api-1
```

### Step 5 — Re-enable Login V2

Run the UPDATE SQL from [#zitadel-login-v2-recovery](#zitadel-login-v2-recovery) step 3.

### Step 6 — Update .env.sops

Follow the SOPS steps from [#zitadel-pat-rotation](#zitadel-pat-rotation) step 4 — add all three new vars.
