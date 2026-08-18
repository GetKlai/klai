---
name: env-scope-guard
description: Enforce the env_file scoping rule across Klai deploy Compose files (SPEC-SEC-ENVFILE-SCOPE-001). Use when adding a service to Compose, wiring service env vars or secrets, or diagnosing an env-scope-guard CI failure.
---

# env-scope-guard (secret scoping in compose)

`deploy/check-env-file-scope.py` blocks a literal bare `.env` under `env_file` in every existing deploy Compose file. It covers scalar values, quoted values, trailing comments, block lists, long list syntax (`path: .env`), and inline or multiline flow lists. `.github/workflows/env-scope-guard.yml` runs the script and its fixtures on pull requests and pushes to `main`; `.githooks/pre-commit` is the local gate.

## The rule

- ❌ `env_file: .env` (or `env_file:` + `- .env`) — this points at `/opt/klai/.env`, the merged SOPS-global file, and leaks **every** Klai secret into that one service's process env. One compromised service = all secrets.
- ✅ `env_file: ./klai-<service>/.env` — per-service scoped file with only that service's secrets (REQ-6).

## When adding a service

1. Give it its own `deploy/klai-<service>/.env` scope; register the secrets in `deploy/SECRETS_MATRIX.md`.
2. Reference only the per-service path in compose.
3. Ship the secret values via `deploy/deploy.sh <service>` (SOPS-decrypt + SSH) — never commit plaintext env files.

## Migrating an existing service

Changing `env_file: .env` to a per-service file changes more than secret
visibility: values previously inherited from the merged global env can fall
back to application defaults without a startup failure.

Before the change, inventory every configured setting from the service's
settings class, Compose mapping, owning SOPS inventory, and current container.
After the change, compare the key set and value equality again. Do not print
secret values in logs or review output; compare presence and non-reversible
fingerprints where direct inspection would expose them. Investigate every
missing or changed value, including non-secret URLs and model names, before
recreating the service. A clean scope check proves only that `.env` is no
longer referenced; it does not prove runtime parity.

## When the check fails

Run `python3 -B deploy/check-env-file-scope.py` locally. Scope every reported reference per service; do not add an allowlist.
