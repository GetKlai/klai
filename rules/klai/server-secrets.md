## Server secrets: read before touching /opt/klai/.env

NEVER modify existing secrets in `/opt/klai/.env` via `sed`, `echo`, or any shell command.
Secrets containing `$` get silently truncated by shell interpolation, breaking all auth.

- **Add new var:** allowed, use single quotes: `echo 'NEW=value' >> /opt/klai/.env`
- **Change existing secret:** ask the user or use SOPS (`klai-claude/docs/patterns/infrastructure.md#sops-secret-edit`)
- **After any change:** verify with `docker exec <container> printenv VAR_NAME`

Full rules: `klai-claude/docs/patterns/infrastructure.md#env-modification-rules`
Full pitfalls: `klai-claude/docs/pitfalls/infrastructure.md#infra-never-modify-env-secrets`
