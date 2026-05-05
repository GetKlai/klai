---
id: SPEC-OBS-002
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
---

# SPEC-OBS-002 — Acceptance Criteria

Given-When-Then scenarios for every requirement in `spec.md`. Edge cases at the bottom, plus the Definition of Done summary.

---

## Scenario's — Authentication

### AC-1: Valid JWT returns 200
**Relates to:** R1, R2, R3

- **Given** a developer with an active Zitadel account in the Klai org and a freshly-issued JWT (audience `vmauth-vlogs`, signed by Zitadel, `exp` in the future).
- **When** they `curl -H "Authorization: Bearer <jwt>" "https://vlogs.{$DOMAIN}/select/logsql/query?query=service:caddy&start=now-1m&end=now&limit=1"`.
- **Then** the response is HTTP 200 with at least one log entry. The same query without the Bearer header returns 401.

### AC-2: JWT signed by another Zitadel app is rejected
**Relates to:** R3

- **Given** a JWT issued by the same Zitadel issuer but for a different application (audience ≠ `vmauth-vlogs`).
- **When** the developer sends it to `vlogs.{$DOMAIN}`.
- **Then** vmauth returns HTTP 401 with a body line clearly mentioning "audience" or "aud" — confirming the rejection reason was *not* signature, but audience.

### AC-3: Expired JWT returns 401
**Relates to:** R1, R4

- **Given** a previously-valid JWT whose `exp` is in the past.
- **When** sent to `vlogs.{$DOMAIN}`.
- **Then** HTTP 401, log line at vmauth records the failure with the user's `sub` claim (so we have an audit trail of attempted-after-expiry).

### AC-4: Tampered JWT returns 401
**Relates to:** R1

- **Given** a JWT with a flipped bit in the body or signature.
- **When** sent to `vlogs.{$DOMAIN}`.
- **Then** HTTP 401.

---

## Scenario's — Refresh-token storage

### AC-5: Refresh-token lands in OS keychain only
**Relates to:** R5

- **Given** a developer running `klai-login` for the first time on macOS.
- **When** the device-code flow completes successfully.
- **Then** macOS Keychain contains an entry with service `klai-vlogs-refresh` and account = the Zitadel `sub` claim. The token value does *not* appear in `~/.zshrc`, `~/.bashrc`, `~/.config/`, the project repo, or the Claude Code session transcript.

### AC-6: Keychain entry is per-user, scoped to login session
**Relates to:** R5

- **Given** the keychain entry from AC-5.
- **When** another macOS user account on the same machine reads `security find-generic-password -s klai-vlogs-refresh`.
- **Then** macOS prompts for the *original* user's password, OR returns "not found", depending on Keychain ACL — both acceptable. What is NOT acceptable: silent read by another user.

### AC-7: Refresh-token TTL is 30 days
**Relates to:** R6

- **Given** a refresh-token issued 31 days ago.
- **When** the launcher tries to use it.
- **Then** Zitadel returns `invalid_grant`. The launcher emits the literal text "Run `klai-login` to authenticate" to stderr and exits non-zero.

---

## Scenario's — Launcher behaviour

### AC-8: First-time happy path
**Relates to:** R8, R10

- **Given** a fresh laptop where `klai-login` has just been run (refresh-token in keychain, nothing else).
- **When** Claude Code starts and the VictoriaLogs MCP is invoked for the first query.
- **Then** within 5 seconds: launcher reads keychain, exchanges refresh-token for JWT against `https://auth.{$DOMAIN}/oauth/v2/token`, spawns `mcp-victorialogs` with `Authorization: Bearer <jwt>` in env, and the user gets log results back. No browser opens, no manual prompt.

### AC-9: Empty keychain → clear error
**Relates to:** R7

- **Given** a laptop where `klai-login` has *not* been run (or where the keychain entry was deleted).
- **When** the launcher starts.
- **Then** the launcher exits with non-zero status, stderr contains the literal phrase "Run `klai-login` to authenticate", and Claude Code surfaces this as an MCP error rather than a silent timeout.

### AC-10: Network failure during refresh → clear error
**Relates to:** R7

- **Given** Zitadel is unreachable (simulated by adding `127.0.0.2 auth.{$DOMAIN}` to `/etc/hosts`).
- **When** the launcher tries to refresh.
- **Then** the launcher emits a network-failure-specific error to stderr (containing the Zitadel URL it tried) and exits non-zero. Does *not* hang indefinitely.

---

## Scenario's — Network isolation

### AC-11: VictoriaLogs has no host port binding
**Relates to:** R11

- **Given** core-01 is running the new compose stack.
- **When** an operator runs `ss -ltnp | grep 9428` on core-01 host.
- **Then** no listening socket is found on the host for port 9428. (VL's port is reachable only from inside the `monitoring` Docker network.)

### AC-12: vmauth has no host port binding
**Relates to:** R12

- **Given** core-01 is running the new compose stack.
- **When** an operator runs `ss -ltnp | grep 8427` on core-01 host.
- **Then** no listening socket is found. vmauth is reachable only via Caddy.

### AC-13: Direct internet access to VL bypasses vmauth → impossible
**Relates to:** R11, R12

- **Given** an attacker on the public internet.
- **When** they try `curl http://core-01.{$DOMAIN}:9428/select/...` or any direct port.
- **Then** the connection times out / is refused. The only exposed query path is `https://vlogs.{$DOMAIN}` via Caddy → vmauth.

---

## Scenario's — Documentation

### AC-14: Fresh-laptop walkthrough
**Relates to:** R13

- **Given** a clean macOS laptop with the Klai repo cloned and the basic dev setup done (the same prerequisites the existing setup-doc lists).
- **When** the developer follows `docs/setup/mcp-servers.md` §8 in order from top to bottom, without consulting the team.
- **Then** they complete `klai-login`, restart Claude Code, and successfully run a VictoriaLogs MCP query — all from the committed docs.

### AC-15: Operator runbook covers all four scenarios
**Relates to:** R14

- **Given** the new section in `.claude/rules/klai/infra/observability.md`.
- **When** an operator reads it.
- **Then** they find concrete procedures for: (a) onboarding a new developer, (b) revoking a developer's access, (c) rotating the vmauth-internal basic-auth secret, (d) debugging a 401 from vmauth.

### AC-16: Tunnel deprecation visible
**Relates to:** R15

- **Given** the new `scripts/victorialogs-tunnel.sh` with the deprecation banner.
- **When** a developer runs it.
- **Then** within the first 3 lines of stderr output, a message appears stating the script is deprecated and pointing to `docs/setup/mcp-servers.md` §8.

---

## Scenario's — Migration & rollback

### AC-17: Soak-period coexistence
**Relates to:** R16

- **Given** Caddy has both `vlogs.{$DOMAIN}` (new) and the legacy SSH-tunnel path active simultaneously during the soak window.
- **When** a developer with the new flow queries via `vlogs.{$DOMAIN}`, AND another developer (or the same one) queries via the old tunnel.
- **Then** both paths return the same data. No interference; no shared state corruption.

### AC-18: One-revert rollback
**Relates to:** R17

- **Given** a serious vmauth/Caddy bug post-cutover that blocks all queries.
- **When** an operator reverts the `.mcp.json` commit and restarts Claude Code.
- **Then** the legacy tunnel + basic-auth path resumes working, with no further infra changes needed (the deployed vmauth and Caddy route remain harmless when unused).

---

## Edge cases

### AC-E1: Clock skew on developer laptop
- **Given** a laptop whose clock is 5 minutes ahead of UTC.
- **When** the launcher exchanges the refresh-token for a JWT.
- **Then** Zitadel issues a JWT whose `iat` is 5 minutes in vmauth's future. vmauth tolerates this within its OIDC `leeway` setting (default 60s in vmauth, but configurable). Document required leeway in the operator runbook.

### AC-E2: Concurrent MCP spawns
- **Given** Claude Code spawns the VictoriaLogs MCP twice in quick succession (rare but possible).
- **When** both launcher instances try to refresh simultaneously.
- **Then** both succeed. Zitadel may treat refresh-tokens as one-time-use and rotate them — if so, the keychain stores the latest. No race-condition state-corruption that wedges either client.

### AC-E3: VL upstream is unreachable from vmauth
- **Given** VL has crashed but vmauth is up.
- **When** a developer queries with a valid JWT.
- **Then** vmauth returns HTTP 502 (not 401). The developer's auth was fine; backend is down. Triggers the existing `container_down` alert from SPEC-OBS-001.

---

## Quality-gate criteria

- **Tested:** all happy-path AC's verified manually with `curl` against staging-vmauth before cutover; AC-9 / AC-10 / AC-13 verified explicitly (negative tests are easy to skip — don't).
- **Readable:** vmauth config file commented; launcher functions named for what they do (`acquireJwt`, `refreshOrFail`, not `getToken`).
- **Unified:** new files follow Klai's existing conventions (Klai-style YAML for vmauth, ESM modules for launcher, project-rule-conformant doc layout).
- **Secured:** every token-handling code path reviewed for accidental log exposure. Use the security-review skill before merge.
- **Trackable:** SPEC-OBS-002-Rxx tags in commit messages on every implementation commit; PR description references this SPEC.

## Definition of Done (cross-references spec.md §"Definition of done")

- [ ] All seven points in `spec.md` "Definition of done" hold.
- [ ] All AC scenarios in this file pass.
- [ ] The four implementation-decision notes (`.moai/specs/SPEC-OBS-002/notes/01-04`) are written and committed.
- [ ] Soak window completed without blocker bugs.
- [ ] Follow-up SPEC opened for `victorialogs-tunnel.sh` removal.
