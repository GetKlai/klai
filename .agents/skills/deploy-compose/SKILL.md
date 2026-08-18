---
name: deploy-compose
description: Explain and verify Klai Compose deployment boundaries (automatic core-01 config sync, validation-only gpu-01 Compose CI, and manual SOPS secret delivery). Use when changing deploy/docker-compose*.yml, Caddyfile, alloy/searxng/vexa configs, Grafana provisioning, or when a deploy did not land.
---

# Deploy flow (compose → core-01)

Two separate halves. Config is CI-synced; secrets are pushed manually.

## Half 1 — config sync (automatic, CI)

`.github/workflows/deploy-compose.yml` runs on pushes to `main` that touch `deploy/docker-compose.yml`, `deploy/docker-compose.override.yml`, the listed core-01 config paths, `deploy/scripts/**`, its smoke test, or the workflow itself:

1. Validates image tags + pullability (`deploy-image-check` skill) and LibreChat patch drift — a failure here stops the deploy before anything reaches the server.
2. SSH-syncs the compose file + configs to **core-01** and runs `scripts/smoke-docker-socket-proxy.sh`. That post-deploy smoke test currently warns on failure but is deliberately non-blocking.

For those exact core-01 paths, merging to `main` is the config deploy. Other code changes use their own service workflows.

## GPU Compose — validation only

`.github/workflows/validate-gpu-compose.yml` validates `deploy/docker-compose.gpu.yml` on pull requests and pushes to `main`. It checks Compose syntax, pinned Vexa tags, and anonymous image pullability. It performs no SSH, sync, or deploy to gpu-01. Follow the relevant runbook and obtain explicit approval before any gpu-01 mutation.

## Half 2 — secrets (manual, deploy.sh)

`deploy/deploy.sh [service]` handles the monorepo's secrets side only: it decrypts `.sops` env files locally (needs `~/.config/sops/age/keys.txt` or `$SOPS_AGE_KEY_FILE`) and pipes them to the configured server over SSH. The compose file itself is version-controlled and contains no secrets. Use `deploy/SECRETS_MATRIX.md` for scope.

## Servers

Topology, IPs, and access live in `klai-infra/SERVERS.md` — that file is the source of truth, don't duplicate it. Key facts: app services run on **core-01**; GPU services (incl. the locally-built transcription-service) run on **gpu-01** and are reverse-tunneled to core-01 via autossh (`/opt/klai/gpu-tunnel-key` on core-01; core-01 reaches them at `172.18.0.1:<port>`).

## When a deploy did not land

1. Inspect the latest run of the same workflow on `main` before attributing a
   failure to the current change. Separate an inherited red-main failure from a
   newly introduced failure.
2. Check the current `deploy-compose` run (`gh run list -w deploy-compose.yml`) — image validation is the usual first failure.
3. Pinned-tag or pullability error → `deploy-image-check` skill.
4. Config synced but service misbehaves → smoke-test output in the same run, then Grafana/VictoriaLogs.
