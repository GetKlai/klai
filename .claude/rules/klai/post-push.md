---
description: Require CI verification and deploy health check after every git push in klai projects
---

# CI Verification & Deploy Health Check After Push

**[HARD] After every `git push` in any klai project, verify that the CI build passes AND the deploy is healthy before declaring the task complete.**

## Why

Multiple times, pushes were declared successful without verifying the CI build. This caused silent deploy failures that only surfaced when users reported missing features. In one case, the GitHub Action completed successfully but the frontend bundle was rsynced to a staging directory — not the directory Caddy actually serves — leaving production unchanged for weeks.

Local build success ≠ CI success. CI success ≠ production rollout.

## Prerequisites

Requires `gh` CLI. Install: `docs/setup/mcp-servers.md` (section 5).

## Step 1: Watch the CI run

Immediately after `git push`:

```bash
gh run watch --exit-status
```

This blocks until the run finishes. Exit code 0 = success, non-zero = failure.

If the push triggered multiple workflows, `gh run watch` prompts to select one. To watch a specific run:

```bash
gh run list --limit 5              # find the run ID
gh run watch <run-id> --exit-status
```

### On CI failure

```bash
gh run view <run-id> --log-failed   # show only the failing step logs
```

Fix the issue, commit, push again, and re-run `gh run watch --exit-status`. Do NOT declare the task complete until CI is green.

## Step 2: Verify server rollout (deploy workflows only)

For workflows that deploy to a server (e.g. `portal-frontend`, `portal-api`), verify the new code is actually running:

### Frontend deploys (portal-frontend)

```bash
# Check that the new JS bundle is on the server in the directory Caddy serves
ssh core-01 "ls -lt /srv/klai-portal/assets/*.js | head -3"

# Verify the bundle contains expected code (e.g. a new feature keyword)
ssh core-01 "grep -l 'expected_keyword' /srv/klai-portal/assets/*.js"
```

The timestamp of the newest `.js` file must match the deploy time. If it is old, the rsync target may be wrong (see pitfall `devops-deploy-path-mismatch`).

### Backend deploys (portal-api)

```bash
# Check container is running and healthy
ssh core-01 "docker ps --filter name=portal-api --format 'table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}'"

# Check recent logs for startup confirmation
ssh core-01 "docker logs --tail 20 klai-core-portal-api-1"

# Hit the health endpoint
ssh core-01 "curl -s http://localhost:8010/health"
```

The container `CreatedAt` must be recent (matching deploy time). The health endpoint must return `{"status":"ok"}`.

## Applies to

All klai projects where a GitHub Action runs on push:

| Project | Action | Deploys to |
|---------|--------|------------|
| `klai-portal/frontend` | `Build and deploy portal-frontend` | core-01 `/srv/klai-portal/` |
| `klai-portal/backend` | `Build and deploy portal-api` | core-01 Docker (portal-api) |
| Any project with `.github/workflows/` | Check the workflow file | See workflow |

## In the sync workflow

After Phase 3 (git push), before Phase 4 (completion report):
1. Run `gh run watch --exit-status`
2. If exit code 0 and workflow deploys: run Step 2 (server rollout check)
3. If exit code 0 and no deploy: continue to Phase 4
4. If exit code non-zero: report build failure to user, DO NOT mark task complete, show the failing Action URL

## Never skip

Do not skip CI verification even if:
- The changes look trivial
- TypeScript compiled cleanly locally
- Previous pushes in the same session succeeded
- Only formatting or comment changes were made
