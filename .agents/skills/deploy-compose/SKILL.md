---
name: deploy-compose
description: How a Klai deploy actually flows (CI sync to core-01, SOPS secrets via deploy.sh). Use when changing deploy/docker-compose*.yml, Caddyfile, alloy/searxng/vexa configs, grafana provisioning, or when a deploy did not land.
---

# Deploy flow (compose → core-01)

Two separate halves. Config is CI-synced; secrets are pushed manually.

## Half 1 — config sync (automatic, CI)

`.github/workflows/deploy-compose.yml` runs on every push to `main` that touches `deploy/docker-compose*.yml`, `deploy/caddy/Caddyfile`, `deploy/alloy/config.alloy`, `deploy/searxng/settings.yml`, `deploy/vexa/profiles.yaml`, `deploy/grafana/provisioning/**`, or `deploy/scripts/**`:

1. Validates image tags + pullability (`deploy-image-check` skill) and LibreChat patch drift — a failure here stops the deploy before anything reaches the server.
2. SSH-syncs the compose file + configs to **core-01** and runs the smoke-test (`scripts/smoke-docker-socket-proxy.sh`).

So: merging to `main` IS the config deploy. There is no separate "deploy step" for compose/config changes — but only for the paths listed above; other code changes deploy through their own service workflows (`klai-connector.yml` etc.).

## Half 2 — secrets (manual, deploy.sh)

`deploy/deploy.sh [service]` handles the secrets side only: it decrypts the `.sops` env files locally (needs your age key at `~/.config/sops/age/keys.txt` or `$SOPS_AGE_KEY_FILE`) and pipes them to the server over SSH. The compose file itself is version-controlled and contains no secrets. Which secret belongs where: `deploy/SECRETS_MATRIX.md`.

## Servers

Topology, IPs, and access live in `klai-infra/SERVERS.md` — that file is the source of truth, don't duplicate it. Key facts: app services run on **core-01**; GPU services (incl. the locally-built transcription-service) run on **gpu-01** and are reverse-tunneled to core-01 via autossh (`/opt/klai/gpu-tunnel-key` on core-01; core-01 reaches them at `172.18.0.1:<port>`).

## When a deploy did not land

1. Check the `deploy-compose` workflow run on GitHub (`gh run list -w deploy-compose.yml`) — image validation is the usual first failure.
2. Pinned-tag or pullability error → `deploy-image-check` skill.
3. Config synced but service misbehaves → smoke-test output in the same run, then Grafana/VictoriaLogs.
