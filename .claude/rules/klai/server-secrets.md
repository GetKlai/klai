## Server secrets & infrastructure

**[HARD] Before ANY work on SOPS, `.env` files, SSH to core-01, deploy scripts, or docker-compose secrets: read BOTH infrastructure files first.**

- `.claude/rules/klai/patterns/infrastructure.md` — how to do it (SOPS commands, env rules, deploy patterns)
- `.claude/rules/klai/pitfalls/infrastructure.md` — what goes wrong (dollar signs, wipes, missing backups)

### Quick rules (always apply)

- **NEVER** modify existing secrets in `/opt/klai/.env` via `sed`, `echo`, or any shell command. Secrets containing `$` get silently truncated by shell interpolation, breaking all auth.
- **Add new var:** allowed, use single quotes: `echo 'NEW=value' >> /opt/klai/.env`
- **Change existing secret:** ask the user or use SOPS (`patterns/infrastructure.md#sops-secret-edit`)
- **After any change:** verify with `docker exec <container> printenv VAR_NAME`
- **AI/CI sessions (no TTY):** use the decrypt-modify-encrypt pattern (`patterns/infrastructure.md#sops-non-interactive`), never `sops edit`
