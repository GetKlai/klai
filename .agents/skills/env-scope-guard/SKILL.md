---
name: env-scope-guard
description: The env_file scoping rule for deploy/docker-compose.yml (SPEC-SEC-ENVFILE-SCOPE-001). Use when adding a service to compose, wiring env vars/secrets for a service, or when the env-scope-guard CI check fails.
---

# env-scope-guard (secret scoping in compose)

`.github/workflows/env-scope-guard.yml` blocks any **bare** `env_file: .env` on a service in `deploy/docker-compose.yml`. Runs on every PR push touching that file (not only at merge), plus direct pushes to main. It is a plain shell grep, deliberately no YAML parser — it also catches the multi-line list form.

## The rule

- ❌ `env_file: .env` (or `env_file:` + `- .env`) — this points at `/opt/klai/.env`, the merged SOPS-global file, and leaks **every** Klai secret into that one service's process env. One compromised service = all secrets.
- ✅ `env_file: ./klai-<service>/.env` — per-service scoped file with only that service's secrets (REQ-6).

## When adding a service

1. Give it its own `deploy/klai-<service>/.env` scope; register the secrets in `deploy/SECRETS_MATRIX.md`.
2. Reference only the per-service path in compose.
3. Ship the secret values via `deploy/deploy.sh <service>` (SOPS-decrypt + SSH) — never commit plaintext env files.

## When the check fails

You (or a merge) reintroduced a bare `.env` reference. Scope it per-service; do not widen the guard's allowlist — the guard has none, by design (SPEC §Threat Model).
