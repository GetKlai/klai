---
id: SPEC-OBS-002
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
---

# SPEC-OBS-002 — Implementation Plan

## Approach in één paragraaf

Land vmauth as a sidecar to VictoriaLogs on the existing `monitoring` Docker network, configured for Zitadel OIDC discovery (issuer `https://auth.{$DOMAIN}`) and audience `vmauth-vlogs`. Add a Caddy route `vlogs.{$DOMAIN}` that pure-`reverse_proxy`'s to vmauth — no Caddy plugins, no `forward_auth` complexity. Vmauth validates JWTs natively against Zitadel's JWKS and injects the existing internal `Authorization: Basic ${VICTORIALOGS_AUTH_*}` upstream toward `victorialogs:9428`, so VL itself doesn't change. On the laptop side, a new `klai-login` Node script runs the OAuth device-code flow against Zitadel and writes the resulting refresh-token to the OS keychain. The existing `.claude/scripts/victorialogs-launcher.mjs` grows ~30 lines that read the refresh-token, exchange it for a fresh JWT before each MCP spawn, and pass it to `mcp-victorialogs` via `VL_INSTANCE_HEADERS`. `.mcp.json` switches `VL_INSTANCE_ENTRYPOINT` to `https://vlogs.{$DOMAIN}` and removes the `${VICTORIALOGS_BASIC_AUTH_B64}` reference. Documentation (mcp-servers.md §8 + new observability runbook) is written in the same PR so the change is reviewable as a single coherent unit. The legacy SSH tunnel script stays available for one release cycle with a deprecation banner; a follow-up SPEC removes it after the soak.

---

## Milestones (priority order — geen tijdsschattingen)

### Milestone 1 — Verify open questions (Priority: High)

**Doel:** lock in the four open questions from `spec.md` so the rest of the work has zero surprises.

Deliverables:
- Smoke-test: send `Authorization: Bearer <fake-jwt>` through `mcp-victorialogs` against a local httpbin to confirm headers propagate. Document result in `.moai/specs/SPEC-OBS-002/notes/01-mcp-bearer-support.md`.
- Decision: separate Zitadel "API" project for `vmauth-vlogs` audience, OR re-use main Klai project with audience claim. Capture rationale in `.moai/specs/SPEC-OBS-002/notes/02-zitadel-app-shape.md`.
- Decision: `keytar` vs platform CLI (`security` / `secret-tool`) for keychain. Capture in `.moai/specs/SPEC-OBS-002/notes/03-keychain-library.md`.
- Re-confirm Caddy `reverse_proxy` (not `forward_auth`) is the right wiring by reading vmauth's own OIDC docs end-to-end one more time.

### Milestone 2 — Server side: vmauth + Caddy + Zitadel app (Priority: High)

**Doel:** `https://vlogs-test.{$DOMAIN}` returns 401 without a JWT and 200 with a valid one. No laptop changes yet.

Deliverables:
- `klai-infra/core-01/vmauth/config.yml` — vmauth users config with OIDC issuer, audience, JWT claim mapping, upstream basic-auth header injection.
- `klai-infra/core-01/docker-compose.yml` — new `vmauth` service on `monitoring` network, mounting the config, no host port binding.
- `klai-infra/core-01/Caddyfile` — new `@vlogs-test host vlogs-test.{$DOMAIN}` block with `reverse_proxy vmauth:8427`. Test hostname first, swap to `vlogs.{$DOMAIN}` in M5.
- Zitadel admin: new API project `klai-vmauth`, native app `vlogs-cli` with grant types `device_code` + `refresh_token`, audience `vmauth-vlogs`, refresh-token TTL 30d. Document the click-path in `.moai/specs/SPEC-OBS-002/notes/04-zitadel-app-setup.md` for replay if we need to recreate.
- Smoke-test plan: `curl` examples for 401 (no token, expired, wrong audience, wrong signature) and 200 (valid).

### Milestone 3 — Laptop side: klai-login + launcher refresh (Priority: High)

**Doel:** `klai-login` works end-to-end, the launcher transparently refreshes JWTs, mcp-victorialogs queries land at vmauth.

Deliverables:
- `.claude/scripts/klai-login.mjs` — device-code flow against Zitadel, writes refresh-token to keychain, prints clear "Done — you can now query logs" on success.
- `.claude/scripts/victorialogs-launcher.mjs` — extended: read refresh-token from keychain → POST to Zitadel `/oauth/v2/token` → set `Authorization: Bearer <jwt>` env var → spawn binary as before. Clean error path when keychain is empty (R7).
- `.mcp.json` — switch entrypoint to `https://vlogs-test.{$DOMAIN}` (still test host until M5), drop the `VICTORIALOGS_BASIC_AUTH_B64` reference for the query path.
- Local smoke: full Claude Code → MCP → vmauth → VL chain returns log data for a known query.

### Milestone 4 — Documentation + deprecation (Priority: High)

**Doel:** anyone — including a new dev who joins next week — can onboard themselves from the committed docs.

Deliverables:
- `docs/setup/mcp-servers.md` §8 fully rewritten: prerequisites, one-time `klai-login` walkthrough with screenshot of the device-code page, env vars no longer needed, refresh cycle explanation, common 401 troubleshooting tree.
- `.claude/rules/klai/infra/observability.md` — new section "VictoriaLogs query auth (operator runbook)": onboarding, revocation, vmauth basic-auth rotation, JWT debugging via `jwt.io`, what to do when Zitadel JWKS is unreachable.
- `scripts/victorialogs-tunnel.sh` — header comment + 5-line stderr deprecation banner pointing to the new flow.
- Cross-link in `CLAUDE.md` (root project rules) so every Claude Code session shows the new flow as the canonical answer to "how do I query logs".

### Milestone 5 — Cutover + soak (Priority: High)

**Doel:** real production hostname `vlogs.{$DOMAIN}` is live, every team-member is onboarded, the legacy path is cold but available.

Deliverables:
- Caddy route renamed from `vlogs-test` to `vlogs` (or run both side-by-side for one cycle, see below).
- `.mcp.json` `VL_INSTANCE_ENTRYPOINT` updated to `https://vlogs.{$DOMAIN}`.
- Team announcement (Slack / wherever) with a one-line "run `klai-login`" instruction.
- Soak window: 1 week with both paths live; collect any complaints; revert plan documented.
- Follow-up SPEC ticket created for tunnel-script removal (do *not* remove in this SPEC).

### Milestone 6 — Quality gates (Priority: High)

Deliverables:
- All EARS requirements from spec.md tied to a concrete acceptance scenario in acceptance.md (already done in this PR).
- Manual run-through of acceptance.md with a fresh laptop or VM image.
- Code review: launcher has no `console.log` of any token (security-review skill checklist).
- Pitfall scan: cross-check against `validator-env-parity (HIGH)`, `verify-changes-landed`, `worktree-for-long-running-changes (HIGH)`. Confirm SPEC implementation lands in a dedicated worktree per project rules.
- Sources cite-check: every external claim about Zitadel / vmauth / Caddy in docs has a working URL.

---

## Files touched (estimate)

| Repo | File | Change |
|---|---|---|
| klai-infra | `core-01/docker-compose.yml` | + vmauth service |
| klai-infra | `core-01/vmauth/config.yml` | new |
| klai-infra | `core-01/Caddyfile` | + vlogs route |
| klai-infra | (Zitadel admin UI) | new app — documented click-path, no committed change |
| Klai | `.mcp.json` | switch entrypoint, drop basic-auth env-ref |
| Klai | `.claude/scripts/victorialogs-launcher.mjs` | + ~30 lines refresh logic |
| Klai | `.claude/scripts/klai-login.mjs` | new |
| Klai | `scripts/victorialogs-tunnel.sh` | deprecation banner |
| Klai | `docs/setup/mcp-servers.md` | §8 rewrite |
| Klai | `.claude/rules/klai/infra/observability.md` | + operator runbook |
| Klai | `CLAUDE.md` (root) | one-line cross-ref |
| Klai | `.moai/specs/SPEC-OBS-002/notes/*` | 4 implementation-time decision logs |

11 files across two repos — well above the 3-file threshold for multi-file decomposition. Per project HARD rule, the implementation runs in a dedicated worktree (`feature/SPEC-OBS-002`).
