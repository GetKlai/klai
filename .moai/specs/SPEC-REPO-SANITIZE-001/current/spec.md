# SPEC-REPO-SANITIZE-001: Sanitize Public Repo for Open-Source

**Status:** Draft
**Created:** 2026-05-13
**Author:** Mark Vletter

---

## Problem Statement

The klai repo is going open-source. The `.claude/rules/klai/` files are valuable — they make AI-assisted development significantly more effective. But some contain production server IPs, SSH paths, Zitadel IDs, and infrastructure details that should not be public.

The goal is NOT to remove these files. It's to **keep the patterns, remove the specifics**.

---

## Approach

Four categories of work:

1. **Sanitize files** — Remove IPs, server names, Zitadel IDs, SSH paths from files that contain dev-relevant patterns
2. **Sanitize code** — Remove hardcoded production IDs from application code defaults
3. **Move** — Relocate purely operational files to klai-infra (private)
4. **Scrub git history** — Remove all sensitive content from past commits so it's not recoverable via `git log -S`

---

## REQ-1: Sanitize Mixed Rule Files (keep patterns, remove specifics)

These files contain architecture patterns that OS developers need, mixed with production details they shouldn't see.

### .claude/rules/klai/infra/servers.md

| Remove | Replace with |
|---|---|
| `65.21.174.162`, `65.109.237.64`, `5.9.10.215` | `<server-ip>` or remove the IP table entirely |
| `ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64` | Generic SSH example |
| `ssh -i /opt/klai/gpu-tunnel-key root@5.9.10.215` | Remove GPU tunnel specifics |
| Hetzner pricing (€47/mo, €17/mo, €100/mo) | Remove — not relevant for devs |

Keep: Portal URL section (`my.getklai.com`), Docker image versioning rules, firewall principles.

### .claude/rules/klai/infra/observability.md

| Remove | Replace with |
|---|---|
| VictoriaLogs SSH tunnel setup + auth credentials | Reference to private ops docs |
| `VICTORIALOGS_AUTH_USER`, `VICTORIALOGS_BASIC_AUTH_B64` | Remove credential names |

Keep: Log pipeline architecture, cross-service trace correlation, key log fields, LogsQL query examples, product events table.

### .claude/rules/klai/infra/deploy.md

| Remove | Replace with |
|---|---|
| GHCR authentication procedures | Remove |
| Server-specific deploy commands (`ssh core-01`) | Remove |
| SOPS references | Remove |

Keep: Alembic migration best practices, CI verification steps, Semgrep false-positive handling, Docker build patterns.

### .claude/rules/klai/platform/zitadel.md

| Remove | Replace with |
|---|---|
| PAT expiry dates (`2027-04-19`) | Remove |
| Zitadel org ID (`362757920133283846`) | `<zitadel-org-id>` |
| All 18-digit Zitadel numeric IDs | `<zitadel-*-id>` |
| Instance-specific feature flag procedures | Remove |

Keep: Service account separation, user grants pattern, portal_users mapping, JWT claim shape, RLS table management.

### .claude/rules/klai/platform/caddy.md

| Remove | Replace with |
|---|---|
| Hetzner DNS auth token reference | Remove |
| Caddy admin + reload procedures | Remove |

Keep: Permissions-Policy, header vs request_header distinction, docker-socket-proxy usage.

### .claude/rules/klai/platform/garage.md

| Remove | Replace with |
|---|---|
| GARAGE_RPC_SECRET reference | Remove |
| Server setup procedures | Remove |

Keep: Config field name differences from docs (critical for S3 development).

### .claude/rules/klai/pitfalls/process-rules.md

| Remove | Replace with |
|---|---|
| `core-01` server references (~15 occurrences) | `<production-server>` or generic "production server" |
| `klai-core-portal-api-1` and similar container names | `<portal-api-container>` or generic |
| Specific commit hashes from incident examples | Remove or shorten to `<commit>` |

Keep: ALL patterns and learnings — every pitfall rule is critical for developers. Only the production-specific identifiers change.

### docs/runbooks/local-dev.md

| Remove | Replace with |
|---|---|
| `362901948573220875` (OIDC Client ID, 8 occurrences) | `<oidc-client-id>` with note "get from team or Zitadel console" |
| `362901948573155339` (App ID) | `<zitadel-app-id>` |
| `362771533686374406` (Project ID) | `<zitadel-project-id>` |
| `362757920133283846` (Org ID) | `<zitadel-org-id>` |
| Zitadel Management API curl with prod URL | Keep the pattern, replace IDs with placeholders |

Keep: All three modes (A/B/C), troubleshooting, daily workflow. The IDs are only needed for Mode A/B (core developers who have Zitadel access and can look them up).

**Acceptance criteria:**
- [ ] `grep -rE '65\.21\.|65\.109\.|5\.9\.10\.' .claude/rules/ docs/runbooks/` returns zero matches
- [ ] `grep -rE 'ssh.*root@|klai_ed25519|gpu-tunnel-key' .claude/rules/` returns zero matches
- [ ] `grep -rE '362757920133283846|362771533686374406|362901948573155339|362901948573220875' .claude/rules/ docs/runbooks/` returns zero matches
- [ ] `grep -c 'core-01' .claude/rules/klai/pitfalls/process-rules.md` returns zero
- [ ] All architecture patterns, coding conventions, and integration guides are preserved
- [ ] A developer using Claude Code on the sanitized repo still gets effective AI assistance

---

## REQ-2: Sanitize Hardcoded Production IDs in Application Code

Zitadel production IDs are hardcoded as **defaults** in the Python settings class. These are not secrets (they're public OIDC identifiers), but they leak the production Zitadel instance structure and should be replaced with empty defaults that force explicit configuration.

### klai-portal/backend/app/core/config.py

| Current | Change to |
|---|---|
| `zitadel_project_id: str = "362771533686374406"` | `zitadel_project_id: str = ""` |
| `zitadel_portal_org_id: str = "362757920133283846"` | `zitadel_portal_org_id: str = ""` |

The existing production validator (`_no_debug_in_production`) already blocks empty values in production. In dev mode (`AUTH_DEV_MODE=true`), these values aren't used. The `.env.example` from SPEC-LOCAL-DEV-001 already provides placeholder values.

**Acceptance criteria:**
- [ ] `grep -rE '362771533686374406|362757920133283846' klai-portal/backend/app/` returns zero matches
- [ ] Backend still starts in dev mode (AUTH_DEV_MODE=true) with empty Zitadel IDs
- [ ] Backend still starts in prod mode with IDs provided via env vars
- [ ] No test regressions

---

## REQ-3: Handle .gitmodules Exposure

`.gitmodules` is tracked in git and reveals private repo URLs:
```
[submodule "klai-private"]
    url = git@github.com:GetKlai/klai-private.git
```

Anyone viewing the public repo will see that `GetKlai/klai-private` exists and contains private content.

Options:
- **Option A**: Remove `klai-private` submodule entirely from `.gitmodules`. Core devs clone it separately.
- **Option B**: Accept the exposure — knowing a private repo exists is low risk. The content is inaccessible without SSH key access.
- **Option C**: Rename the submodule to something generic (`klai-ops` or `private`).

Recommendation: **Option A** — remove `klai-private` from `.gitmodules`. It's already gitignored via the submodule mechanism; core devs can clone it into `klai-private/` manually. Also consider whether `klai-infra` should stay as a public submodule reference (the repo URL suggests it might need to be private).

**Acceptance criteria:**
- [ ] Decision made and documented on which submodules stay/go
- [ ] If removing: submodule cleanly removed, `.gitmodules` updated
- [ ] Core developer instructions updated for manual clone

---

## REQ-4: Sanitize .gitleaks.toml

Remove rotated tokens from the allowlist. They're not active but reveal token formats.

**Acceptance criteria:**
- [ ] No real token values (even rotated) in `.gitleaks.toml`
- [ ] Allowlist entries use pattern descriptions instead of literal values

---

## REQ-5: Move Purely Operational Files to klai-infra

These files have zero relevance for application developers and belong in the private repo.

| File | Destination in klai-infra |
|---|---|
| `.claude/rules/klai/infra/monitoring.md` | `klai-infra/docs/rules/monitoring.md` |
| `.claude/rules/klai/infra/sops-env.md` | `klai-infra/docs/rules/sops-env.md` |
| `.claude/rules/klai/platform/vllm.md` | `klai-infra/docs/rules/vllm.md` |

Runbooks to move (keep `local-dev.md` in public repo):

| File | Destination |
|---|---|
| `docs/runbooks/platform-recovery.md` | `klai-infra/docs/runbooks/` |
| `docs/runbooks/gpu-01-setup.md` | `klai-infra/docs/runbooks/` |
| `docs/runbooks/uptime-kuma.md` | `klai-infra/docs/runbooks/` |
| `docs/runbooks/credential-rotation.md` | `klai-infra/docs/runbooks/` |
| `docs/runbooks/sec-022-egress-capture.md` | `klai-infra/docs/runbooks/` |
| `docs/runbooks/rls-upgrade.md` | `klai-infra/docs/runbooks/` |
| `docs/runbooks/alerting-rollout.md` | `klai-infra/docs/runbooks/` |
| `docs/runbooks/post-mortems/*` | `klai-infra/docs/runbooks/post-mortems/` |

Runbooks that STAY in public repo (dev-relevant):

| File | Why |
|---|---|
| `docs/runbooks/local-dev.md` | Developer setup guide |
| `docs/runbooks/provisioning-retry.md` | Documents state machine that devs interact with |
| `docs/runbooks/ms-docs-oauth.md` | OAuth integration guide for connector devs |
| `docs/runbooks/widget-integration.md` | Widget embedding guide |
| `docs/runbooks/version-management.md` | Version management for all devs |
| `docs/runbooks/mfa-check-failed.md` | Auth troubleshooting for devs |
| `docs/runbooks/auth-failure-burst.md` | Auth debugging guide |

**Acceptance criteria:**
- [ ] Moved files exist in klai-infra
- [ ] Moved files are deleted from the public repo
- [ ] Remaining runbooks and rules still load correctly in Claude Code
- [ ] No broken cross-references between remaining files

---

## REQ-6: Scrub Git History

Use BFG Repo-Cleaner to remove all sensitive content from past commits. This is the final step before making the repo public — once done, all collaborators must force-pull.

### Patterns to scrub

| Category | Specific values |
|---|---|
| Server IPs | `65.21.174.162`, `65.109.237.64`, `5.9.10.215` |
| Zitadel prod IDs | `362771533686374406`, `362757920133283846`, `362901948573155339`, `362901948573220875` |
| SOPS age public keys | `age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur` and other `age1*` strings found in runbooks |
| Rotated tokens | Values from `.gitleaks.toml` allowlist (e.g., `sk-bNLQ61Qs533P7GOzfXdyxA`, `eea93826eed5f725daa3d27383f090d116c6f6e3`) |
| .env files | Verify with `git log --all --diff-filter=A -- '*.env' '*/.env'` — scrub any that contain real values |
| Old .env.example | Previous version with hardcoded prod Zitadel IDs as defaults |

### Procedure

1. Merge ALL open PRs first (history rewrite invalidates them)
2. Create a backup: `git clone --mirror` before running BFG
3. Run BFG with replacement patterns
4. `git reflog expire --expire=now --all && git gc --prune=now --aggressive`
5. Force-push to GitHub
6. All team members: `git fetch --all && git reset --hard origin/main`
7. Request GitHub support to purge cached commits (or recreate repo if timing allows)

**Acceptance criteria:**
- [ ] `git log -p --all -S '65.21.174.162'` returns zero results
- [ ] `git log -p --all -S '362771533686374406'` returns zero results
- [ ] `git log -p --all -S 'age1lyd243'` returns zero results
- [ ] `git log -p --all -S 'sk-bNLQ61Qs'` returns zero results
- [ ] All team members have force-pulled successfully
- [ ] GitHub cached commits are purged

---

## REQ-7: Verify Claude Code Effectiveness Post-Sanitize

After sanitization, verify that Claude Code still works effectively with the remaining rules.

Test scenarios:
- [ ] "Fix a bug in portal-api auth" — Claude loads process-rules, portal-backend, portal-security rules
- [ ] "Add a new connector" — Claude loads docker-socket-proxy, litellm, observability rules
- [ ] "Update frontend component" — Claude loads portal-patterns, styleguide rules
- [ ] "Debug a cross-service issue" — Claude loads observability (sanitized), trace correlation patterns

**Acceptance criteria:**
- [ ] Claude Code provides the same quality of guidance on sanitized repo as on current repo
- [ ] No rule file references other files that were moved to klai-infra
- [ ] Sanitized placeholders (`<server-ip>`, `<zitadel-org-id>`) don't confuse Claude into hallucinating values

---

## Implementation Order

1. **REQ-5** — Move purely operational files to klai-infra first (simplest, no editing)
2. **REQ-1** — Sanitize mixed rule files and runbooks (careful editing, keep patterns)
3. **REQ-2** — Remove hardcoded Zitadel IDs from config.py
4. **REQ-3** — Handle .gitmodules (decision + execution)
5. **REQ-4** — Clean .gitleaks.toml
6. **REQ-6** — Scrub git history (destructive, do LAST, after all PRs merged)
7. **REQ-7** — Verify Claude Code effectiveness

---

## Risks

| Risk | Mitigation |
|---|---|
| Git history rewrite breaks open PRs | Do REQ-6 after ALL PRs are merged, as the final step before going public |
| Sanitized rules lose useful context | REQ-7 verification step; can always add generic context back |
| klai-infra rules don't auto-load for core devs | Core devs symlink `klai-infra/docs/rules/` into `.claude/rules/klai/infra-private/` (gitignored path) |
| Moved runbooks lose cross-references | Audit all internal links between remaining and moved files before moving |
| Empty Zitadel ID defaults break existing deployments | Production reads from env vars (never defaults); dev mode doesn't use them. Add startup validation that rejects empty IDs when `AUTH_DEV_MODE=false` |
| GitHub caches old commits even after force-push | Contact GitHub support to purge, or recreate repo (transfer stargazers/issues) |
| BFG misses a pattern | Run acceptance criteria grep checks AFTER BFG, before force-push. Add any missed patterns and re-run |
| `<placeholder>` values in rules confuse Claude | Test in REQ-7; if problematic, use descriptive comments instead of angle-bracket placeholders |

---

## Files Inventory (complete)

### Files that are fine as-is (no changes needed)

- `.claude/rules/klai/platform/docker-socket-proxy.md` — pure architecture, no sensitive content
- `.claude/rules/klai/platform/librechat.md` — integration patterns, no sensitive content
- `.claude/rules/klai/platform/litellm.md` — tier aliases and routing, no sensitive content
- `.claude/rules/klai/pitfalls/process-rules.md` — after REQ-1 sanitize of `core-01` references
- `.claude/rules/klai/projects/*.md` — all dev-relevant, no sensitive content
- `.claude/rules/klai/design/*.md` — all dev-relevant, no sensitive content
- `.claude/rules/klai/lang/*.md` — all dev-relevant, no sensitive content
- `.claude/rules/klai/serena.md` — tool integration, no sensitive content
- `.claude/rules/klai/no-ask-user-question.md` — workflow rule, no sensitive content
- `.claude/rules/klai/codeindex.md` — tool integration, no sensitive content

### Files to sanitize (REQ-1)

- `.claude/rules/klai/infra/servers.md`
- `.claude/rules/klai/infra/observability.md`
- `.claude/rules/klai/infra/deploy.md`
- `.claude/rules/klai/platform/zitadel.md`
- `.claude/rules/klai/platform/caddy.md`
- `.claude/rules/klai/platform/garage.md`
- `.claude/rules/klai/pitfalls/process-rules.md`
- `docs/runbooks/local-dev.md`

### Files to move to klai-infra (REQ-5)

- `.claude/rules/klai/infra/monitoring.md`
- `.claude/rules/klai/infra/sops-env.md`
- `.claude/rules/klai/platform/vllm.md`
- `docs/runbooks/platform-recovery.md`
- `docs/runbooks/gpu-01-setup.md`
- `docs/runbooks/uptime-kuma.md`
- `docs/runbooks/credential-rotation.md`
- `docs/runbooks/sec-022-egress-capture.md`
- `docs/runbooks/rls-upgrade.md`
- `docs/runbooks/alerting-rollout.md`
- `docs/runbooks/post-mortems/*`
