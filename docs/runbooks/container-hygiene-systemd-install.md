# Container Hygiene — systemd timer install (one-time per host)

> Commands are given as they run on core-01, from the deploy directory (the one holding `docker-compose.yml`). See [How public runbooks write commands](../contributing/infrastructure-boundary.md#how-public-runbooks-write-commands).

> SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-5 + REQ-6.
>
> Activates the daily safe-cleanup timer and the weekly orphan-audit
> timer on core-01. Required once per host; the deploy-compose.yml
> workflow syncs the unit files but does NOT run `systemctl` (no sudo
> via the SSH-action key).

## Prerequisites

- `deploy-compose.yml` workflow has run at least once after this SPEC's
  PR merged → unit files are present at `systemd/*.{service,timer}` in the deploy directory
- `deploy/scripts/*.sh` are at `scripts/*.sh` in the deploy directory (same workflow)
- Operator access to core-01 with sudo (see the private infrastructure runbooks)

## Race-condition note (fresh-server setup)

On a brand-new server, `deploy-compose.yml` MUST run BEFORE any
service-specific deploy workflow (`portal-api.yml`, `docs.yml`, etc.)
because the latter call `compose-up.sh` (in `scripts/` of the deploy directory) which is
synced by the former. On an existing server with the scripts already
present, ordering does not matter — pulls happen idempotently.

If you ever clone klai onto a new host, run the deploy-compose.yml
workflow first (or sync the scripts manually with the install commands
below) before triggering any other deploy.

## Install

```bash
# Verify files are present (synced by deploy-compose.yml)
ls -l systemd/
ls -l scripts/

# Install via symlink so future deploy-compose.yml syncs propagate
# without re-running this runbook.
sudo ln -sf "$PWD/systemd/docker-cleanup.service" /etc/systemd/system/docker-cleanup.service
sudo ln -sf "$PWD/systemd/docker-cleanup.timer"   /etc/systemd/system/docker-cleanup.timer
sudo ln -sf "$PWD/systemd/orphan-audit.service"   /etc/systemd/system/orphan-audit.service
sudo ln -sf "$PWD/systemd/orphan-audit.timer"     /etc/systemd/system/orphan-audit.timer

# Reload systemd to pick up new units
sudo systemctl daemon-reload

# Enable + start (--now starts immediately + on every boot)
sudo systemctl enable --now docker-cleanup.timer
sudo systemctl enable --now orphan-audit.timer

# Verify
systemctl status docker-cleanup.timer
systemctl status orphan-audit.timer
systemctl list-timers | grep -E 'docker-cleanup|orphan-audit'
```

Expected output: both timers `active (waiting)`, next run scheduled at
03:00 (cleanup daily, audit Sundays).

## Smoke-test

```bash
# Dry-run the cleanup logic (without prune-flag)
docker image prune --dry-run -f 2>&1 | head -5

# Run the audit ad-hoc to verify it emits events to the journal
sudo systemctl start orphan-audit.service && \
  sudo journalctl -u orphan-audit.service --since '1 minute ago' | tail -20
```

You should see structlog-shaped JSON lines on stdout, including
`"event":"audit_run_completed"` with a `total_events` count. Negative
finding (zero orphan events) is the desired post-deploy state once
all stages of the SPEC have landed.

## Uninstall (rollback)

```bash
sudo systemctl disable --now docker-cleanup.timer orphan-audit.timer && \
  sudo rm -f /etc/systemd/system/docker-cleanup.{service,timer} \
             /etc/systemd/system/orphan-audit.{service,timer} && \
  sudo systemctl daemon-reload
```

The unit files in `systemd/` of the deploy directory remain (not deleted by uninstall).

## Querying audit-events from VictoriaLogs

```
service:klai-orphan-audit AND _time:[now-7d,now]
```

In Grafana: add a Logs panel with this LogsQL on the `victorialogs`
datasource. For alerts: fire on `event:caddy_upstream_missing` or
`event:tenant_container_no_route` with `severity:critical`.

Querying via Claude Code: VictoriaLogs MCP tool with the same query.
