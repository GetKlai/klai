# Pitfalls

> Mistakes we've made. Don't repeat them.

## Structure

Shared pitfall files cover mistakes relevant across all Klai projects (DevOps, infrastructure).
Project-specific pitfalls live in each project's own `docs/pitfalls/` directory.

## Categories (shared)

| Category | File | Entries |
|----------|------|---------|
| [Process](pitfalls/process.md) | AI dev workflow, testing discipline, minimal changes | 13 entries |
| [Git](pitfalls/git.md) | Destructive commands, secrets in commits | 4 entries |
| [DevOps](pitfalls/devops.md) | Coolify, Docker, deployments, services | 0 entries |
| [Infrastructure](pitfalls/infrastructure.md) | Hetzner, SOPS, env vars, DNS, SSH | 2 entries |
| [Platform](pitfalls/platform.md) | LiteLLM, vLLM, LibreChat, Zitadel, Caddy, Grafana | 12 entries |

## Project pitfalls

| Project | Category | File |
|---------|----------|------|
| klai-website | Frontend | [klai-website/docs/pitfalls/frontend.md](../../klai-website/docs/pitfalls/frontend.md) *(planned)* |

## How to use

**Before deploying**: Scan `pitfalls/devops.md` and `pitfalls/infrastructure.md`
**Before platform work**: Read `pitfalls/platform.md` for the relevant component
**After an incident**: Run `/retro "what happened"` to capture it immediately

## Quick Reference

### Process (13)

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| `process-validate-before-code-change` | **HIGH** | Fixing a bug | Validate hypothesis BEFORE changing code |
| `process-verify-completion-claims` | **CRIT** | AI reports task complete | Verify with git diff, don't trust |
| `process-server-restart-protocol` | **CRIT** | Restarting a service | NEVER use run_in_background for servers |
| `process-test-user-facing-not-imports` | **HIGH** | Completing a fix | Test actual endpoints, not just imports |
| `process-debug-logging-first` | **HIGH** | Investigating API errors | Add logging FIRST, look at real data |
| `process-trust-user-feedback` | **CRIT** | User says it's broken | STOP testing, investigate exact scenario |
| `process-read-spec-first` | **CRIT** | Starting a SPEC task | Read the full SPEC before implementing |
| `process-minimal-changes` | **HIGH** | Any task | Only what was asked, nothing more |
| `process-wait-after-question` | **HIGH** | After asking a question | STOP, wait for answer — do not continue |
| `process-listen-before-acting` | **CRIT** | User explains a problem | Read the FULL explanation before acting |
| `process-ask-before-retry` | **HIGH** | Operation failed 2x | STOP, summarize findings, ask before retrying |
| `process-debug-data-before-theory` | **HIGH** | Investigating a bug | Check actual data BEFORE forming theories |
| `process-verify-full-flow` | **HIGH** | Multi-step bugfix | Verify ALL downstream steps, not just the one you touched |

### Git (4)

| ID | Sev | Rule |
|----|-----|------|
| `git-no-destructive-commands` | **CRIT** | Never reset --hard / clean -f without confirmation |
| `git-no-secrets-in-commits` | **CRIT** | Never commit .env or credentials |
| `git-commit-specific-files` | **HIGH** | Stage specific files, not git add . |
| `git-verify-before-commit` | **HIGH** | Always git diff --staged before committing |

### DevOps

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| *(add with `/retro` after incidents)* | | | |

### Infrastructure (2)

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| `infra-env-not-synced` | **HIGH** | After adding to config.sops.env | Also update Coolify env vars manually |
| `infra-sops-missing-main-env` | **HIGH** | After setting up SOPS for services | Also encrypt the main docker-compose .env in SOPS |

### Platform (12)

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| `platform-litellm-vllm-provider-prefix` | **HIGH** | LiteLLM → vLLM config | Use `hosted_vllm/` prefix, not `openai/` |
| `platform-litellm-drop-params` | **HIGH** | LiteLLM → vLLM config | Add `drop_params: true` in litellm_settings |
| `platform-vllm-gpu-memory-utilization` | **CRIT** | vLLM startup on H100 | Use ~0.55 (32B) + ~0.40 (8B), not original values |
| `platform-vllm-sequential-startup` | **CRIT** | Starting vLLM instances | Start 32B first, wait healthy, then 8B |
| `platform-vllm-mps-enforce-eager` | **HIGH** | vLLM + MPS | Add --enforce-eager to 8B instance |
| `platform-librechat-oidc-reuse-tokens` | **CRIT** | LibreChat OIDC config | Never set OPENID_REUSE_TOKENS=true |
| `platform-librechat-username-claim` | **HIGH** | LibreChat OIDC config | Always set OPENID_USERNAME_CLAIM=preferred_username |
| `platform-librechat-logout-no-zitadel-session` | **HIGH** | Implementing logout | LibreChat logout doesn't end Zitadel session |
| `platform-grafana-victorialogs-loki-incompatible` | **HIGH** | Adding VictoriaLogs to Grafana | Use victoriametrics-logs-datasource plugin |
| `platform-caddy-cloud86-no-plugin` | **HIGH** | Wildcard TLS setup | Cloud86 has no Caddy plugin — use Hetzner DNS |
| `platform-caddy-not-auto-routing` | **HIGH** | Adding a new tenant | Caddy doesn't auto-discover containers |
| `platform-rag-api-non-lite-image` | **HIGH** | RAG deployment (Phase 2) | Use non-lite image for TEI embeddings |

---

## Severity Levels

| Level | Meaning |
|-------|---------|
| **CRIT** | Can cause data loss, break production, or require major rework |
| **HIGH** | Causes significant problems or wasted time |
| **MED** | Causes minor issues, easy to fix |

## Adding new pitfalls

1. Run `/retro "description"` — manager-learn handles everything
2. Or manually: add entry to the appropriate category file
3. If project-specific, add to `[project]/docs/pitfalls/` instead
4. Update quick reference table above with ID, severity, and one-line rule
