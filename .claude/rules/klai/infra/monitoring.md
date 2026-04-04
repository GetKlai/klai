---
paths:
  - "klai-infra/core-01/scripts/push-health.sh"
  - "klai-infra/core-01/scripts/gpu-health.sh"
---
# Monitoring & Status Page (status.getklai.com)

<!-- Keywords: status.getklai.com, uptime kuma, push-health, status page, monitoring -->

## Status page philosophy (CRIT)
Klai's status page (status.getklai.com) is intentionally transparent. All services and
functionality are visible to users — never remove monitors or groups from the public page.
Users should understand what powers the platform and what might be affected during incidents.

- **Show functionality, not servers.** "Backup" and "Monitoring" belong. "gpu-01" as a bare
  server name does not — expose the services it runs (Embeddings, Reranker, Transcription).
- **Never touch existing groupings** without explicit request. The current groups (Products,
  AI & ML, Knowledge Pipeline, Data & Storage, Platform, Infrastructure) are deliberate.
- **Adding** new monitors: always welcome. **Removing** monitors: never without explicit ask.

## Product-service parent mapping
Each product (push monitor) has child services that it depends on. A monitor can only have
one parent in Uptime Kuma. Assign to the **primary** consumer when a service is shared.

Before modifying parent mappings, read the current state from the DB first — previous
sessions may have set these up carefully.

## push-health.sh
- Runs every minute via cron (`* * * * * /opt/klai/scripts/push-health.sh`).
- `set -eo pipefail` — any failure kills the entire script. Guard against empty variables
  and malformed URLs (newlines, special chars in curl args).
- `resolve_container <service>` uses compose labels. Service names must match
  `com.docker.compose.service` exactly — verify with `docker ps --format`.
- Missing `KUMA_TOKEN_*` → silent skip (by design). New monitors need token in `/opt/klai/.env`
  AND in SOPS (`klai-infra/core-01/.env.sops`) to survive `deploy.sh main`.
