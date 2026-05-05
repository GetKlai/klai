---
id: SPEC-OBS-002
version: "0.2.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-OBS-001 (alerting infra — same Grafana/VL stack, different surface)
  - SPEC-SEC-IDENTITY-ASSERT-001 (Zitadel JWT pattern between internal services)
  - SPEC-SEC-ENVFILE-SCOPE-001 (precedent for moving secrets out of `.env`)
roadmap: docs/setup/mcp-servers.md
---

## HISTORY

| Version | Date       | Author        | Change |
|---------|------------|---------------|--------|
| 0.1.0   | 2026-05-05 | Mark Vletter  | Initial draft. Replaces SSH tunnel + shared `VICTORIALOGS_BASIC_AUTH_B64` with vmauth + Zitadel OIDC + per-developer refresh-token. |
| 0.2.0   | 2026-05-05 | Mark Vletter  | Milestone 1 PoC findings folded back. Architectural validity confirmed via vendor docs + source. Three Klai-specific findings: (a) vmauth in production needs Linux/Docker — Mac local-test exhibits Go-HTTP timeout + silent `0 users` parse — won't fix locally. (b) **Login V2 does not auto-engage for new Zitadel apps** — falls back to V1 UI which renders empty in Klai's setup. New open question SPEC-OBS-002-Q5. (c) Zitadel `client_credentials` on freshly-created API apps returns `invalid_client` even after projection delay — root cause unknown without deeper Zitadel debugging. None blocks the architecture; all become M2-investigation items in target environment. PoC artefacts removed; findings captured in `notes/01-05`. Recommendation: switch device-code → authorization-code-with-PKCE + localhost redirect (industry-standard CLI pattern) to side-step Q5 entirely. |

# SPEC-OBS-002: VictoriaLogs developer-query access via Zitadel OIDC + vmauth

## Context

Today every Klai developer queries production logs through:

1. `./scripts/victorialogs-tunnel.sh` — SSH tunnel from laptop → core-01 → docker-internal `victorialogs:9428`. Resolves the container IP dynamically because Docker assigns a new IP on each restart.
2. `mcp-victorialogs` MCP server with `Authorization: Basic ${VICTORIALOGS_BASIC_AUTH_B64}` — a single shared base64-encoded `vlogs:<password>` credential, decrypted from `klai-infra/core-01/.env.sops` and stored in every developer's `~/.zshrc`.

The setup has six concrete weaknesses, in descending impact:

1. **No per-user attribution.** Every query authenticates as the same `vlogs` user. The Caddy/VictoriaLogs access logs cannot answer "which developer ran this query at 02:41". For a system that holds production secrets, customer IDs, OAuth flows, and PII — this is an audit gap.
2. **Long-lived shared secret.** The base64 password sits in plaintext in `~/.zshrc` on every laptop. One developer's laptop compromise = full log access for the attacker until the team rotates the secret AND every laptop's `.zshrc` is updated. There is no automatic rotation.
3. **Manual revocation.** A leaving developer's access is removed by rotating `VICTORIALOGS_AUTH_PASSWORD`, re-encrypting SOPS, redeploying VL, and chasing every remaining developer to update `.zshrc`. In practice this is rarely done — the secret keeps working.
4. **Tunnel is a per-developer responsibility.** Each laptop must remember to run `victorialogs-tunnel.sh` in a dedicated terminal. Forgotten tunnels = silent MCP failures = lost diagnostic time. Documented but real.
5. **Container IP fragility.** The tunnel resolves the container IP on each start. A VL restart between laptop boots invalidates the tunnel.
6. **Onboarding friction.** A new developer needs (a) SSH access to core-01, (b) the SOPS-encrypted base64 secret, (c) the tunnel script, (d) understanding of the launcher conventions. Four steps that all involve handing out shared secrets.

This SPEC replaces the entire stack with a Zitadel-issued OIDC pattern that VictoriaMetrics' own documentation describes as the production-grade approach for `vmauth`. The result: per-developer identity, automatic short-lived JWTs, Zitadel-backed revocation, no SSH tunnel, no shared `.zshrc` secret.

## Why now

1. We just added a sixth secret (`VICTORIALOGS_BASIC_AUTH_B64`) to a developer's shell rc-file (this conversation, 2026-05-05). The pattern is starting to scale poorly.
2. SPEC-SEC-IDENTITY-ASSERT-001 already established Zitadel-JWT as Klai's preferred internal identity proof. Extending the same identity to *humans* (instead of services) is a natural progression.
3. The Caddy stack already has a near-identical pattern in production (`logs-ingest.{$DOMAIN}`) — Bearer-token gate at Caddy + basic-auth header injection upstream. We are duplicating it for the query path with a stronger token semantic.

## Goal

A Klai developer queries production logs by:

1. Once: running `klai-login` on their laptop (~30s, device-code flow, browser opens).
2. Forever after: opening Claude Code. The MCP launcher transparently exchanges a refresh-token for a fresh JWT and authenticates against `https://vlogs.{$DOMAIN}` over plain HTTPS — no SSH, no tunnel, no shared secret in shell rc.

The system identifies queries by Zitadel user, supports immediate revocation, and stores the refresh-token in the OS keychain (macOS Keychain, Windows Credential Manager, libsecret on Linux).

## Scope

### In scope

- Deployment of `vmauth` as an authenticated proxy in front of `victorialogs:9428` on the `monitoring` Docker network.
- Caddy route `vlogs.{$DOMAIN}` → `vmauth` with TLS termination.
- Zitadel API project + native application configured for OIDC device-code flow + refresh-token grant, with audience claim that `vmauth` will validate.
- New `klai-login` CLI script (Node, lives in `.claude/scripts/` since it's used by the launcher) that performs device-code flow and writes the refresh-token to OS keychain.
- Updated `victorialogs-launcher.mjs`: read refresh-token from keychain → POST to Zitadel `/oauth/v2/token` for fresh JWT → set `VL_INSTANCE_HEADERS` to `Authorization=Bearer <jwt>` → spawn `mcp-victorialogs`.
- `.mcp.json` switched to `https://vlogs.{$DOMAIN}` as the entrypoint, no per-developer base64 secret required.
- Documentation rewrite: `docs/setup/mcp-servers.md` §8, plus new section in `.claude/rules/klai/infra/observability.md` covering onboarding, revocation, refresh-token rotation, and debugging.
- Deprecation notice on `scripts/victorialogs-tunnel.sh` (kept functional for one release cycle as fallback, removed in follow-up SPEC).

### Out of scope

- VictoriaLogs **ingestion** path (`logs-ingest.{$DOMAIN}`) — stays on the existing static Bearer-token (Alloy on public-01 has no Zitadel client and pushing service-tokens is harder than reading them).
- Grafana log-querying — already authenticated via Grafana SSO + datasource proxy.
- Linux developer keychain integration beyond `libsecret` — we assume libsecret is available on Klai-issued Linux laptops; if not, that's a per-laptop fix outside this SPEC.
- Removal of the SSH tunnel script. Deprecation only; removal is a clean follow-up SPEC after a soak window.

## Requirements (EARS)

### Authentication (REQ-1 .. REQ-4)

- **SPEC-OBS-002-R1** [HARD] WHEN a developer queries `https://vlogs.{$DOMAIN}/select/...`, vmauth SHALL validate the request's `Authorization: Bearer <jwt>` header against Zitadel's JWKS endpoint. Invalid signature, expired `exp`, wrong `iss`, or wrong `aud` SHALL result in HTTP 401.
- **SPEC-OBS-002-R2** [HARD] vmauth SHALL inject `Authorization: Basic <vlogs-internal-creds>` upstream toward `victorialogs:9428` so VictoriaLogs's own `-httpAuth.username/password` continues to work without modification. The internal basic-auth credential is *not* exposed to developers.
- **SPEC-OBS-002-R3** [HARD] The Zitadel application SHALL use audience `vmauth-vlogs` (or equivalent), and vmauth SHALL reject JWTs whose `aud` does not contain that value. This prevents JWT-misuse from other Klai apps that share the same Zitadel issuer.
- **SPEC-OBS-002-R4** [HARD] The JWT TTL SHALL be ≤1 hour. Disabling a developer in Zitadel SHALL block new queries within max(JWT TTL, 1 hour).

### Refresh-token storage (REQ-5 .. REQ-7)

- **SPEC-OBS-002-R5** [HARD] The refresh-token SHALL be stored in the OS keychain (macOS Keychain, Windows Credential Manager, Linux libsecret), under a service name `klai-vlogs-refresh` and account name = the Zitadel `sub` claim. It SHALL NOT be written to `.zshrc`, `.env`, plain files in `~/.config`, or any committed location.
- **SPEC-OBS-002-R6** [HARD] The refresh-token SHALL have TTL ≤30 days configured in Zitadel. Re-running `klai-login` SHALL be the documented refresh path after that window.
- **SPEC-OBS-002-R7** WHEN the refresh-token is missing, expired, or revoked, the launcher SHALL emit a clear error message to stderr with the literal text "Run `klai-login` to authenticate" and exit non-zero so Claude Code surfaces the failure.

### Launcher behaviour (REQ-8 .. REQ-10)

- **SPEC-OBS-002-R8** [HARD] On every Claude Code MCP startup, `victorialogs-launcher.mjs` SHALL exchange the refresh-token for a fresh JWT via `POST https://auth.{$DOMAIN}/oauth/v2/token` with `grant_type=refresh_token`, then set `VL_INSTANCE_HEADERS=Authorization=Bearer <jwt>` in the spawned `mcp-victorialogs` process environment.
- **SPEC-OBS-002-R9** WHEN the cached JWT is older than 50 minutes (10-minute safety margin before the 1h TTL), the launcher SHALL refresh proactively before forwarding the next request. Caching may be implemented in-process (single MCP session) or skipped entirely (refresh on every spawn) — implementation choice.
- **SPEC-OBS-002-R10** The launcher SHALL forward the existing `VL_INSTANCE_ENTRYPOINT` env var so `mcp-victorialogs` reaches `https://vlogs.{$DOMAIN}` instead of `localhost:9428`.

### Network isolation (REQ-11 .. REQ-12)

- **SPEC-OBS-002-R11** [HARD] VictoriaLogs SHALL remain on the private `monitoring` Docker network with no host port binding. Only `vmauth` and Caddy on the same network reach it.
- **SPEC-OBS-002-R12** [HARD] vmauth SHALL be reachable only via Caddy. Its container port SHALL NOT be bound to the core-01 host.

### Documentation (REQ-13 .. REQ-15)

- **SPEC-OBS-002-R13** `docs/setup/mcp-servers.md` §8 SHALL be rewritten to describe: (a) one-time `klai-login` flow, (b) what Keychain entry is created, (c) how to rotate / re-authenticate after 30 days, (d) common failure modes.
- **SPEC-OBS-002-R14** A new section in `.claude/rules/klai/infra/observability.md` SHALL document for Klai operators: how to onboard a new developer (Zitadel user grant), how to revoke (Zitadel user disable + force-logout), how to rotate the vmauth-internal basic-auth credential, and how to debug a 401 from vmauth.
- **SPEC-OBS-002-R15** `scripts/victorialogs-tunnel.sh` SHALL be marked deprecated via a header comment + a stderr warning when executed, pointing to the new flow.

### Migration & rollback (REQ-16 .. REQ-17)

- **SPEC-OBS-002-R16** During soak, the new `vlogs.{$DOMAIN}` host SHALL coexist with the old SSH-tunnel path. `.mcp.json` switches to the new endpoint after the soak window (length to be agreed at implementation time, default 1 week).
- **SPEC-OBS-002-R17** Rollback SHALL be a single revert: restore the previous `.mcp.json` (basic-auth + localhost) and re-enable the deprecated tunnel script. vmauth and the Caddy route can stay deployed without harm.

## Open questions to resolve at implementation time

Status as of v0.2.0 reflects M1 PoC outcomes. Full detail in `notes/05-poc-findings.md`.

1. **`mcp-victorialogs` Bearer support.** ✅ **Resolved (docs + source review, 2026-05-05).** Binary parses `VL_INSTANCE_HEADERS` as `key=value` comma-separated and applies them verbatim to outgoing HTTP. Official docs explicitly cite "custom headers for authentication, e.g. behind reverse proxy". `Authorization=Bearer <jwt>` will propagate. See `notes/01-mcp-bearer-support.md`.
2. **Zitadel audience claim mechanics.** ⏳ **Deferred to M2.** Native + Device Code app type creates fine via Management API (verified). Final shape (separate API project vs audience-on-existing-project) waits on resolving Q5. See `notes/02-zitadel-app-shape.md`.
3. **`klai-login` keychain library.** ⏳ **Deferred to M3.** Both `keytar` and platform-CLI (`security` / `secret-tool`) are viable; pick during M3 launcher implementation. See `notes/03-keychain-library.md`.
4. **Caddy → vmauth wiring.** ✅ **Resolved (vmauth source `app/vmauth/jwt.go`, 2026-05-05).** Pure `reverse_proxy` to vmauth; vmauth validates JWT natively via `oidc.issuer` + `match_claims`. No `forward_auth` complexity needed.
5. **Login V2 engagement for new Zitadel apps.** 🆕 **NEW from M1 — single largest open risk for M3.** Klai's production OIDC flows route through Login V2 (portal-hosted UI on `my.getklai.com`), but a freshly-created Zitadel app via the Management API does **not** auto-engage Login V2. Browser-based device-code falls back to legacy V1 console UI which renders an empty form in Klai's setup. Three candidate solutions to evaluate in M2:
   - **5a.** App-level Login V2 opt-in via Management API (existence of such a knob in Zitadel v4 needs confirmation — may not exist).
   - **5b.** Instance-level Login V2 base_uri rewriting to handle device-code paths (would affect ALL apps — risky, likely rejected).
   - **5c.** Switch from device-code → **authorization-code-with-PKCE + localhost redirect** (industry-standard for desktop CLI tools — `gh-cli`, `aws sso login`, etc.). Reliably routes through Login V2 for OIDC apps because it's a normal browser-redirect flow. Recommended.
   See `notes/02-zitadel-app-shape.md` for reproduction steps.

## Risks

- **Risk: Zitadel device-code flow UX.** First-time login has to open a browser tab. If Zitadel's device-code page is slow or buggy, every developer onboarding suffers. Mitigation: smoke-test before announcing SPEC-002 to the team; have rollback ready.
- **Risk: Refresh-token leak via launcher logs.** If the launcher accidentally `console.log`s the refresh-token or JWT, it ends up in Claude Code's terminal and possibly VictoriaLogs itself (recursive disclosure). Mitigation: review every log statement in the launcher; never log the token, only token *length* on debug.
- **Risk: vmauth JWT validation lag.** vmauth fetches Zitadel JWKS on startup and rotates per OIDC spec. If Zitadel rotates keys faster than vmauth refreshes, a small window of valid JWTs gets rejected. Acceptable: vmauth re-fetches on signature mismatch per docs. Smoke-test before declaring done.
- **Risk: Multi-developer concurrent refresh.** Zitadel may rate-limit refresh-token grants per client. Unlikely with one client per laptop; mitigation: backoff in launcher.
- **Risk: macOS Keychain prompts in headless contexts.** Some macOS Keychain reads trigger a UI prompt (`security` CLI without ACL config). Mitigation: configure ACL on first write so subsequent reads are silent for the launcher binary.

## Definition of done

The SPEC is implemented when **all** of the following hold:

1. A new Klai developer can run a single shell command (`klai-login`), complete a browser flow once, and immediately use the VictoriaLogs MCP from Claude Code without further manual configuration.
2. Disabling that developer in Zitadel blocks their next query within 1 hour.
3. The Caddy/vmauth/VL chain returns 401 for every JWT-less, expired, signature-invalid, or wrong-audience request.
4. `~/.zshrc` no longer needs `VICTORIALOGS_BASIC_AUTH_B64` for query access. (Ingestion-side bearer stays.)
5. `docs/setup/mcp-servers.md` and `.claude/rules/klai/infra/observability.md` are updated and accurate.
6. The deprecation warning fires when running `victorialogs-tunnel.sh`.
7. End-to-end test from a fresh laptop (or simulated VM) completes the full flow without any team-internal documentation outside the committed docs.
