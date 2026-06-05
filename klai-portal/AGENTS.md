# klai-portal

Inherits the root `AGENTS.md`. Rules below apply to everything under
`klai-portal/`. Backend- and frontend-specific gates live in
`backend/AGENTS.md` and `frontend/AGENTS.md`.

## Deploy DoD (CRIT)

After every commit to klai-portal:

1. `git push`
2. `gh run watch --exit-status` — wait for the GitHub Action to complete.
3. Verify server rollout — check bundle timestamp or container age on core-01.

**Never claim something is deployed before BOTH CI is green AND the new code is
confirmed on the server.** Do not run `portal-deploy.sh` manually — the GitHub
Action handles it. ("CI green" alone is not "deployed".)

## Tenant & user lifecycle runbooks

Stateful flows with their own state machines — read the runbook before changing
them, and follow the identity gate in `backend/AGENTS.md`:

- Tenant provisioning: `docs/runbooks/provisioning-retry.md` (state machine,
  retry endpoint, stuck-detector — SPEC-PROV-001).
- Tenant deprovisioning: `docs/runbooks/tenant-delete.md` (16-step orchestrator,
  owner self-service delete, platform-admin endpoints — SPEC-INFRA-TENANT-DELETE-001).
- User offboarding: `docs/runbooks/user-offboarding.md` (disposition wizard,
  KB transfer/delete, auto-revoke of API keys + MCP tokens, suspend-vs-offboard
  — SPEC-PORTAL-KB-OWNERSHIP-001).
