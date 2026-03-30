---
paths:
  - "**/.env*"
  - "klai-infra/**"
  - "**/Caddyfile"
  - "**/docker-compose*.yml"
---
# Infrastructure Pitfalls

> Hetzner, SOPS, environment variables, DNS, SSH

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [infra-env-not-synced](#infra-env-not-synced) | HIGH | SOPS and Coolify are not auto-synced; update both |
| [infra-sops-missing-main-env](#infra-sops-missing-main-env) | HIGH | Service-specific SOPS needs local `.sops.yaml` |
| [infra-sops-dotenv-dollar-sign](#infra-sops-dotenv-dollar-sign) | HIGH | `$` in secrets breaks SOPS dotenv; use YAML format |
| [infra-env-bash-special-chars](#infra-env-bash-special-chars) | CRIT | Unquoted `(`, `)`, `&` in `.env` break `source`; quote all values with special chars |
| [infra-docker-user-container-ip-stale](#infra-docker-user-container-ip-stale) | CRIT | Container IPs change on restart; never hardcode in iptables |
| [infra-zitadel-console-http-api](#infra-zitadel-console-http-api) | CRIT | Zitadel console broken; use Management API directly |
| [infra-caddy-no-global-csp](#infra-caddy-no-global-csp) | HIGH | Global CSP `header {}` blocks browser APIs silently |
| [infra-never-modify-env-secrets](#infra-never-modify-env-secrets) | CRIT | Never `sed`/`echo` existing secrets in `.env` |
| [infra-sops-files-in-subdirs](#infra-sops-files-in-subdirs) | CRIT | SOPS files in subdirs need a local `.sops.yaml` |
| [infra-sops-incomplete-wipes-server](#infra-sops-incomplete-wipes-server) | CRIT | SOPS with fewer vars than server wipes production on sync |
| [infra-sync-env-no-safety-checks](#infra-sync-env-no-safety-checks) | CRIT | Secrets sync without safety guards is a ticking time bomb |
| [infra-placeholder-values-in-sops](#infra-placeholder-values-in-sops) | CRIT | Placeholder values in SOPS break services silently |
| [infra-kuma-tokens-not-in-containers](#infra-kuma-tokens-not-in-containers) | CRIT | KUMA_TOKEN vars are cron-only; invisible to container-based recovery |
| [infra-push-health-set-u-total-blackout](#infra-push-health-set-u-total-blackout) | HIGH | One missing KUMA_TOKEN crashes entire monitoring script |

---

## infra-env-not-synced

**Severity:** HIGH

**Trigger:** After adding a new variable to `config.sops.env` and redeploying

SOPS and Coolify are NOT automatically synchronized. Adding a variable to the SOPS file does not make it available to running services.

**What went wrong:**
Service fails after deploy because new env var is missing at runtime, even though it exists in SOPS.

**Why it happens:**
Coolify reads env vars from its own UI configuration, not from `config.sops.env` directly. The SOPS file is the source of truth for secrets management, but Coolify has its own separate env var store.

**Prevention:**
1. After adding to SOPS: also add the same variable in Coolify → Service → Environment Variables
2. Trigger a fresh redeploy after adding env vars
3. Check application logs after deploy to verify the var is being read

**See also:** `patterns/devops.md#coolify-env-update`

---

## infra-sops-missing-main-env

**Severity:** HIGH

**Trigger:** After setting up SOPS for service-specific configs (caddy, litellm, zitadel)

The docker-compose main `.env` file can exist on the server as plaintext only, not backed up in SOPS. If the server is lost, all database passwords, JWT secrets, and OIDC client secrets are gone permanently.

**What went wrong:**
Service-specific `.env.sops` files (caddy, litellm, zitadel) were set up early. The main `/opt/klai/.env` used by docker-compose was generated on the server and never added to SOPS. Disaster recovery would require rotating every secret.

**Why it happens:**
The main `.env` is a flat file that accumulates variables over time. It doesn't map cleanly to one service, so it's easy to overlook when setting up SOPS.

**Prevention:**
`core-01/.env.sops` must be the encrypted backup of `/opt/klai/.env`. Whenever a new secret is added to the server `.env`, update the SOPS file first:
```bash
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops core-01/.env.sops
./core-01/deploy.sh main
```

**See also:** `patterns/infrastructure.md#sops-secret-edit`

---

## infra-sops-dotenv-dollar-sign

**Severity:** HIGH

**Trigger:** Adding a secret containing `$` characters (bcrypt hashes, generated passwords) to a SOPS dotenv file that is used as a docker-compose `.env` file

Bcrypt hashes (e.g. `$2a$14$...`) and some generated secrets contain `$` signs. Docker-compose interprets `$` in `.env` files as variable interpolation. A single `$` causes docker-compose to treat the rest as a variable name, substituting an empty string. The service starts with a blank value and may silently fail or behave incorrectly.

**What went wrong:**
```
GRAFANA_CADDY_HASH=$2a$14$nNgmBc87...
# docker-compose reads this as: GRAFANA_CADDY_HASH= (empty)
# because $2a, $14, $nNgmBc87... are treated as variable expansions
```

**Why it happens:**
SOPS stores the raw plaintext value. When `deploy.sh` decrypts and writes to `/opt/klai/.env`, the raw value (with single `$`) lands in the `.env` file. Docker-compose then interpolates it and the value becomes empty.

**Fix for `.env` on the server:**
```bash
# Use $$ (double dollar) in the .env file for literal $ characters
GRAFANA_CADDY_HASH=$$2a$$14$$nNgmBc87dq1Dkx5XPIVR1...
```

**Fix for storage in `.env.sops`:**
Store the value with `$$` so `deploy.sh` writes the correct escaped form to the server `.env`:
```bash
GRAFANA_CADDY_HASH=$$2a$$14$$nNgmBc87dq1Dkx5XPIVR1...
```

**Diagnosis:**
```bash
# If a service has a blank env var that should contain a hash:
docker exec <container> env | grep <VAR_NAME>
# Expected: $2a$14$...
# Symptom: empty string or variable name fragment
```

**Prevention:**
- When generating bcrypt hashes or any secret with `$`, immediately store it with `$$` in `.env.sops`
- After decrypting and writing to server `.env`, verify the variable value with `docker exec` before restarting dependent services

---

## infra-env-bash-special-chars

**Severity:** CRIT

**Trigger:** Adding a secret whose value contains bash special characters — `(`, `)`, `&`, `!`, `;`, `|`, `<`, `>` — to `.env.sops` without quoting it

All CI deploy scripts start with `source /opt/klai/.env`. If any value contains unquoted bash special characters, bash raises a syntax error and the entire deploy fails — for **every** service, not just the one whose secret is new.

**What happened (March 2026):**
`GLITCHTIP_SECRET_KEY` was added to SOPS without quotes. The value contained `(` and `)`. Every CI deploy job started failing immediately after `source /opt/klai/.env`:

```
/opt/klai/.env: line 32: syntax error near unexpected token `)'
Process exited with status 2
```

This blocked all service deploys on core-01 for the duration of the incident.

**Fix:**
Wrap any value containing special chars in double quotes in `.env.sops`:

```bash
# WRONG — breaks source
GLITCHTIP_SECRET_KEY=jxIt)%wLF+6gWKpvDzBYmEci#)FGpbuirsv#_&YRwkRCmdIN(O

# CORRECT — double quotes prevent bash parse errors
GLITCHTIP_SECRET_KEY="jxIt)%wLF+6gWKpvDzBYmEci#)FGpbuirsv#_&YRwkRCmdIN(O"
```

**Double quotes are safe when the value does NOT contain `$` or backticks.** If the value contains `$`, use the `infra-sops-dotenv-dollar-sign` pattern instead (use `$$` to escape).

**Prevention:**
Before adding any new secret to `.env.sops`, check if the value contains: `( ) & ! ; | < >`
If yes → wrap in double quotes. When in doubt, always quote.

**Quick diagnosis:**
```bash
# Test that .env can be sourced on the server before declaring deploy fixed
ssh core-01 "bash -c 'source /opt/klai/.env && echo OK'"
```

---

## infra-docker-user-container-ip-stale

**Severity:** CRIT

**Trigger:** Using container IPs in DOCKER-USER iptables rules, then restarting any container in the stack

Docker assigns container IPs dynamically from the bridge subnet. IPs are stable while the stack is running but change when containers restart. DOCKER-USER rules written with a specific container IP (`172.18.0.x`) break silently when the container gets a new IP after a restart.

**What went wrong:**
```bash
# Rules were saved with Caddy at 172.18.0.9:
iptables -A DOCKER-USER -d 172.18.0.9 -i enp5s0 -p tcp -m multiport --dports 80,443 -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -j DROP

# After container restart, Caddy moved to 172.18.0.7.
# All inbound HTTPS traffic now hits the DROP rule — services unreachable from internet.
# push monitors still pass (they run from inside the server), so the outage is not obvious.
```

**Symptoms:**
- External monitors (Uptime Kuma HTTP) timeout; push monitors stay green
- `curl https://chat.getklai.com` times out from an external host
- `docker ps` shows all containers running and healthy
- `curl https://chat.getklai.com` works from within core-01 (bypasses DOCKER-USER)

**Correct approach (port-based rules):**
```bash
# Flush and re-add with port matching, not container IP
iptables -F DOCKER-USER
iptables -A DOCKER-USER -i enp5s0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -p tcp -m multiport --dports 80,443 -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -p udp --dport 443 -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -j DROP
iptables-save > /etc/iptables/rules.v4
```

Port-based rules match any container on those ports. Docker's own DOCKER chain ensures only explicitly-mapped containers receive the traffic — no additional security risk.

**Script:** `core-01/scripts/harden-docker-user.sh` — run after `docker compose up -d` to (re)apply correct rules.

**Why Docker's DOCKER chain is not enough alone:**
Docker adds ACCEPT rules in the FORWARD chain's DOCKER chain (for mapped ports) and then DROP. Without DOCKER-USER restrictions, all forwarded traffic passes through. Internal networks (Redis, PostgreSQL) are safe because they have no port mappings. But the intent of DOCKER-USER is to be explicit about what external traffic is allowed.

---

## infra-zitadel-console-http-api

**Severity:** CRIT

**Trigger:** Zitadel console is broken -- all API calls fail, blank or non-functional admin UI at `auth.getklai.com`

**Symptom:**
`/ui/console/assets/environment.json` returns `"api":"http://auth.getklai.com"` instead of `"https://"`. The Angular console makes gRPC-Web calls to `http://` URLs from an HTTPS page. Browsers block these as mixed content. The moment any CSP is present (even Zitadel's own), `connect-src 'self'` on an HTTPS page does not match `http://` URLs.

**Root cause:**
`--tlsMode disabled` as a CLI flag in the compose `command:` overrides `ZITADEL_EXTERNALSECURE=true`. These are different internal config keys. The startup logs will show:

```
External Secure: false   <-- wrong, means api URL will be http://
```

Even though `ZITADEL_EXTERNALSECURE=true` is set, the CLI flag wins.

**Fix:**
1. Remove `--tlsMode disabled` from the compose `command:`
2. Add `ZITADEL_TLS_ENABLED: "false"` as an explicit environment variable (this is the env var equivalent -- it tells Zitadel not to terminate TLS itself, which is correct behind a proxy)
3. Use `h2c://zitadel:8080` in the Caddy reverse_proxy (official Zitadel/Caddy pattern for HTTP/2 cleartext)

```yaml
# docker-compose.yml
zitadel:
  command: start-from-init --masterkey ${ZITADEL_MASTERKEY}  # no --tlsMode disabled
  environment:
    ZITADEL_TLS_ENABLED: "false"      # replaces --tlsMode disabled
    ZITADEL_EXTERNALSECURE: "true"    # public URL is https://
    ZITADEL_EXTERNALDOMAIN: auth.getklai.com
    ZITADEL_EXTERNALPORT: "443"
```

```caddyfile
# Caddyfile
handle @auth {
    reverse_proxy h2c://zitadel:8080
}
```

**Verification:**
```bash
curl -s https://auth.getklai.com/ui/console/assets/environment.json
# Must show: "api":"https://auth.getklai.com"
# Wrong:     "api":"http://auth.getklai.com"
```

Check startup logs for confirmation:
```bash
docker logs klai-core-zitadel-1 2>&1 | grep "External Secure"
# Must show: External Secure: true
```

**Do NOT fix this with:**
- `header_up X-Forwarded-Proto "https"` in Caddy (does not override the CLI flag)
- CSP changes (`upgrade-insecure-requests`, adding `http:` to `connect-src`) -- these mask the symptom and introduce security regressions
- Restarting Zitadel without changing the command/env -- the flag is read at startup

---

## infra-caddy-no-global-csp

**Severity:** HIGH

**Trigger:** Adding a Content-Security-Policy header to Caddy's global `header {}` block

A global CSP at the reverse proxy level is not industry standard and will break applications that manage their own CSP. Zitadel, LibreChat, and similar apps set application-specific CSP headers. A generic Caddy CSP overrides these and breaks the apps.

**What breaks:**
Any app whose own CSP is more permissive than the generic Caddy CSP will fail. Zitadel's Angular console in particular makes gRPC-Web calls that require `connect-src 'self' auth.getklai.com` — a generic CSP without this exact allowance blocks all API calls.

**Correct split:**
Caddy's global `header {}` block is appropriate for **transport-level security headers** that are safe to apply globally:
```caddyfile
header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
    X-Content-Type-Options "nosniff"
    X-Frame-Options "SAMEORIGIN"
    Referrer-Policy "strict-origin-when-cross-origin"
    Permissions-Policy "geolocation=(), microphone=(), camera=()"
    -Server
}
```

Do NOT add `Content-Security-Policy` here. Each application sets its own.

---

## infra-never-modify-env-secrets

**Severity:** CRIT

**Trigger:** Any session that needs to add, change, or fix a variable in `/opt/klai/.env` on core-01

NEVER modify existing secret values (PATs, API tokens, passwords) in `/opt/klai/.env` via shell commands like `echo >>`, `sed -i`, or manual editing. These operations are fragile: dollar signs get interpolated, values get truncated, and the result is a silently broken deployment where every auth call returns 401.

**What has gone wrong (multiple times):**
1. A session used `echo "VAR=value$with_dollar"` -- the shell ate the `$with_dollar` part
2. A session used `sed -i` to update a PAT -- the replacement value was wrong/truncated
3. The portal-api kept running with the corrupted PAT, returning 401 on every login attempt
4. Diagnosing the issue is hard because the server appears healthy and the PAT "looks" valid

**Rules:**
1. **NEVER overwrite** `PORTAL_API_ZITADEL_PAT`, `PORTAL_API_DB_PASSWORD`, or any other existing secret
2. **Adding a NEW variable** is allowed but must use single-quoted values: `echo 'NEW_VAR=value' >> /opt/klai/.env`
3. If a secret needs changing, tell the user to do it manually or use SOPS
4. After ANY `.env` change, verify the value inside the container: `docker exec <container> printenv VAR_NAME`

**If login breaks with "Errors.Token.Invalid":**
The PAT in the `.env` is almost certainly corrupted. Ask the user for the correct value from the Zitadel console. Do NOT assume the PAT expired -- it expires in 2030.

---

## infra-sops-files-in-subdirs

**Severity:** CRIT

**Trigger:** Bulk-deleting directories from klai-infra (e.g. during a repo cleanup or restructuring)

Service-specific `.env.sops` files live inside service directories (`core-01/caddy/.env.sops`, `core-01/litellm/.env.sops`, `core-01/zitadel/.env.sops`). When those directories are deleted wholesale with `git rm -r`, the `.env.sops` files inside go with them — silently.

**What went wrong:**
During monorepo consolidation, `git rm -r core-01/caddy/ core-01/litellm/ core-01/zitadel/` removed the service configs (which had moved to the public monorepo) but also deleted the service-specific encrypted secret files that had no other backup.

**Why it is dangerous:**
The main `core-01/.env.sops` contains shared docker-compose secrets but does NOT contain all service-specific secrets. The Hetzner DNS token (caddy), LiteLLM key + Mistral API key (litellm), and Zitadel masterkey + Postgres passwords (zitadel) are only in their respective `.env.sops` files. Losing them requires rotating all of those secrets.

**Recovery:**
```bash
# Restore from git history (works as long as repo history is intact)
git checkout HEAD~1 -- core-01/caddy/.env.sops core-01/litellm/.env.sops core-01/zitadel/.env.sops
git checkout HEAD -- <any other files incorrectly restored>
git commit -m "fix: restore accidentally deleted .env.sops files"
```

**Prevention:**
When bulk-deleting service directories, always check first:
```bash
find core-01/ -name "*.sops*" | sort
```
Never delete a directory that contains a `.sops` file without explicitly moving that file first.

---

## infra-sops-incomplete-wipes-server

**Severity:** CRIT

**Trigger:** Running `sync-env.yml` when `.env.sops` has fewer variables than the server's `/opt/klai/.env`

A secrets sync workflow that overwrites the server `.env` with a decrypted SOPS file will destroy every variable that exists on the server but not in SOPS. If SOPS has 35 vars and the server has 82, the sync wipes 47 vars — taking down Caddy (TLS), portal-api, LiteLLM, and every service that depends on the missing vars.

**What happened (March 2026):**
The server's `/opt/klai/.env` had accumulated 82 vars over months of manual additions and provisioning. The SOPS file only contained the initial 35 vars, many with placeholder values. A push to `klai-infra/main` triggered `sync-env.yml`, which did a blind `cat >` overwrite. Result:
- Caddy lost `HETZNER_AUTH_API_TOKEN` and `ADMIN_EMAIL` — all HTTPS endpoints down
- LiteLLM lost `MISTRAL_API_KEY` — all AI features broken
- Portal-api crashed (Zitadel unreachable behind dead Caddy)
- SearXNG, docs-app, Grafana, Gitea — all degraded

**Why it happens:**
The SOPS file was treated as a partial backup rather than the authoritative source. Manual `echo >> /opt/klai/.env` additions on the server were never back-ported to SOPS. Over time, the gap between SOPS (35 vars) and server (82 vars) grew silently until the next sync wiped the difference.

**Prevention:**
1. SOPS must be the COMPLETE source of truth — every var on the server must exist in SOPS with a real value
2. After adding any var to the server manually, immediately add it to `.env.sops` and push
3. The sync workflow must have a threshold check: abort if the new file has significantly fewer vars than the current one (see `infra-sync-env-no-safety-checks`)
4. Periodically audit: `ssh core-01 "wc -l /opt/klai/.env"` vs `sops -d .env.sops | wc -l`

**See also:** `pitfalls/infrastructure.md#infra-sync-env-no-safety-checks`, `patterns/devops.md#sops-env-sync`

---

## infra-sync-env-no-safety-checks

**Severity:** CRIT

**Trigger:** A secrets sync workflow (CI or manual script) that writes to a server `.env` without validation

A sync workflow that does `sops -d .env.sops | ssh server "cat > /opt/klai/.env"` with no safety checks is a production outage waiting for any of: a SOPS decryption failure (writes empty file), an incomplete SOPS file (wipes vars), or a network drop during transfer (writes partial file).

**Why it happens:**
The initial workflow was written as a simple decrypt-and-copy. It worked fine when SOPS and the server were in sync. No one added guards because the workflow ran infrequently and "always worked." The first time SOPS diverged from the server, it caused a major outage.

**Prevention — required guards for any secrets sync workflow:**
1. **Minimum line count** — abort if decrypted file has fewer than N lines (catches decryption failure producing empty output)
2. **Var count threshold (90%)** — abort if new file has significantly fewer vars than the current server file
3. **Critical vars validation** — check that essential vars (e.g. `HETZNER_AUTH_API_TOKEN`, `MISTRAL_API_KEY`, `PORTAL_API_ZITADEL_PAT`) are present, non-empty, and not placeholders
4. **Masked diff output** — log ADDED/REMOVED/CHANGED key names (never values) so operators can review
5. **Key removal block** — if vars would be removed, abort on push-trigger; require manual `workflow_dispatch` with explicit confirmation
6. **Atomic write** — write to `.env.new`, chmod, then `mv` (never `cat >` directly to `.env`)
7. **Post-deploy server verification** — after writing, verify critical vars on the actual server
8. **Backup rotation** — keep N most recent backups of `.env` before overwriting

**See also:** `patterns/devops.md#sops-env-sync`, `patterns/devops.md#atomic-env-deploy`

---

## infra-placeholder-values-in-sops

**Severity:** CRIT

**Trigger:** SOPS file contains placeholder values like `PLACEHOLDER_VOER_IN`, `placeholder_generate_later`, `CHANGE_ME`, or empty strings for required variables

Placeholder values in SOPS are silent production bombs. They pass simple existence checks (`grep -q "^VAR="`) and even non-empty checks (`-n "$VAR"`) but break the actual service when it tries to use the value. The service starts, reads the placeholder, and fails at the first real operation (API call, TLS handshake, database connect).

**What happened (March 2026):**
Seven variables in `.env.sops` had placeholder values (`PLACEHOLDER_VOER_IN` or `placeholder_generate_later`). When the sync workflow deployed this file, services received these strings as their actual config. SearXNG got a fake secret key, VictoriaLogs got a placeholder auth token, Grafana got a non-bcrypt admin hash. Each service failed in a different way — some crashed, some started but rejected all requests.

**Why it happens:**
During initial setup, placeholder values are added as reminders to fill in later. "Later" never comes because the services work fine with their manually-set server values. The SOPS file drifts further from reality with each manual addition, and the placeholders sit dormant until the next sync.

**Prevention:**
1. Never commit a SOPS file with placeholder values — generate real values at the time of adding the variable
2. Add a CI check that rejects common placeholder patterns: `grep -E '(PLACEHOLDER|placeholder|CHANGE_ME|TODO|FIXME|xxx)' .env.sops && exit 1`
3. The sync workflow's critical vars validation must check for placeholder patterns, not just non-empty values:
   ```bash
   if echo "$value" | grep -qiE '(placeholder|change_me|generate_later)'; then
     echo "ABORT: $key has a placeholder value"
     exit 1
   fi
   ```
4. When adding a new service that needs secrets, generate the real values immediately (use `openssl rand -base64 32` or the service's own key generation tool)

**See also:** `pitfalls/infrastructure.md#infra-sops-incomplete-wipes-server`

---

## infra-kuma-tokens-not-in-containers

**Severity:** CRIT

**Trigger:** Recovering `/opt/klai/.env` from running Docker containers after a wipe or corruption

`KUMA_TOKEN_*` variables (29 of them) are only used by the `push-health.sh` cron script, not by any Docker container. When recovering `.env` by running `docker exec <container> printenv` across all containers, these tokens are completely invisible — no container has them in its environment.

**What happened (March 2026):**
After the `.env` wipe, all 82 vars were recovered from running containers. The 15 KUMA_TOKEN vars that happened to also be in Docker environments were recovered, but 14 were not — they existed solely for the cron script. The push-health.sh script crashed on the first missing token (`KUMA_TOKEN_CHAT: unbound variable`), killing ALL monitoring for 17+ hours. The status page showed everything red despite all services being healthy.

**Other non-container vars to watch for:**
- `GRAFANA_CADDY_HASH` — used in Caddyfile basic_auth, not a container env var
- Any variable only referenced by scripts in `/opt/klai/scripts/`

**Recovery procedure for KUMA_TOKEN vars:**
```bash
# Extract all push tokens from Uptime Kuma's SQLite DB on public-01
ssh public-01 "docker exec $(docker ps -qf name=uptime-kuma) \
  sqlite3 /app/data/kuma.db \
  \"SELECT name, push_token FROM monitor WHERE type='push' ORDER BY name;\""
```
Then map monitor names to `KUMA_TOKEN_*` variable names using `push-health.sh` as reference.

**Prevention:**
1. SOPS must contain ALL `.env` vars, including non-container ones
2. After any `.env` recovery, run `push-health.sh` manually to verify monitoring works
3. Audit: `grep -oP 'KUMA_TOKEN_\w+' /opt/klai/scripts/push-health.sh | sort -u` vs `grep -c KUMA_TOKEN /opt/klai/.env`

**See also:** `pitfalls/devops.md#devops-recover-secrets-from-running-containers`, `runbooks/uptime-kuma.md`

---

## infra-push-health-set-u-total-blackout

**Severity:** HIGH

**Trigger:** Any `KUMA_TOKEN_*` variable missing from `.env` when `push-health.sh` runs

`push-health.sh` uses `set -euo pipefail`. The `-u` flag causes bash to abort on any unset variable. Some token references in the script use `${VAR:-}` (safe default), but others use bare `${VAR}` or `$VAR`. If ANY bare-referenced token is missing, the entire script crashes before it can push ANY heartbeat — causing a total monitoring blackout across all 37 monitors.

**Symptoms:**
- `status.getklai.com` shows ALL services red
- All 41 containers are running and healthy
- `/opt/klai/logs/health.log` has no new entries (script crashes before writing)
- Running the script manually shows: `line 76: KUMA_TOKEN_CHAT: unbound variable`

**Diagnosis:**
```bash
# Run manually to see the crash
ssh core-01 "bash /opt/klai/scripts/push-health.sh"

# Check when logging stopped
tail -5 /opt/klai/logs/health.log
```

**Fix:** Add the missing token to `.env` (recover from Uptime Kuma DB — see `infra-kuma-tokens-not-in-containers`), then run the script manually to verify.

**Prevention:** All `KUMA_TOKEN_*` references in `push-health.sh` should use `${VAR:-}` syntax so a missing token skips that one monitor instead of crashing the entire script.

---

*(Add more entries here with `/retro "description"` after infrastructure incidents.)*
