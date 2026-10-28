# Automatic rollback

Alerts: `auto_rollback_event`, `rollback_rehearsal_missing`,
`public01_infra_deploy_rollback`, `public01_infra_deploy_silent` (all
`severity=critical`). Spec: SPEC-INFRA-AUTO-ROLLBACK-001.

`deploy-with-rollback.sh` (one service image) and `compose-rollback.sh` (the
compose and config sync) put the previous state back when a deploy gate goes
red. Each writes a report to `/opt/klai/deploy-reports/<service>.json`
(`compose.json` for the sync), the container log next to it as `.log`, a line
in the ledger `/opt/klai/deploy-ledger.log`, and, for a rollback, an
`auto_rollback` event line that Alloy ships to VictoriaLogs.

## A deploy was rolled back

The mail names the service, the failed gate and the outcome.

- `rolled_back`: the previous version is serving again. Nothing is broken now.
  The bad version is still on `main`, so the next deploy of that service fails
  the same way until it is fixed. Open the issue labelled `auto-rollback`
  (title `CI failure: <workflow>`): it carries the report, the commit and the
  failed run. Fix forward on `main`; there is no revert PR.
- `rollback_failed`: the previous version did not hold either, or there was no
  previous image. Production may be broken. The issue is labelled
  `rollback-failed`. On core-01 read `<service>.json` and `<service>.log` in
  `/opt/klai/deploy-reports/`, then `docker compose ps <service>` and decide:
  restore by hand (`docker tag klai/<service>:previous <compose tag>`, then
  `compose-up.sh --no-pull --force-recreate <service>`) or fix and redeploy.
  Images that migrate their own database (Twenty, LiteLLM, GlitchTip, Zitadel)
  report `rollback_failed` on purpose and are never restored by image alone.

In VictoriaLogs:
`_time:30m service:auto-rollback event:auto_rollback | sort by (_time) desc | limit 10`.

## The weekly rehearsal is missing

No `rollback_rehearsal` event with outcome `ok` reached VictoriaLogs for 8
days. The three checks, in order, are in the alert description. A green rehearsal
run whose line is missing means Alloy or VictoriaLogs is the break, not the
rollback.

## A public-01 infra deploy was rolled back

public-01 is not deployed by this repo's CI: its `deploy-infra.sh` (root cron,
every 10 minutes, script in the private klai-infra repo) applies klai-infra
`public-01/` from main. Before installing it copies every file to `<file>.prev`;
when the post-apply checks fail (Kuma healthy, Alloy running, getklai.com and
status.getklai.com answering) it puts them back, brings the stack up, checks
again and writes the revision to `/opt/klai/.infra-bad-rev`. No later run
reapplies that revision; a newer one on main is tried normally. The mail names
the revision, the failed gate and the outcome. The line is
`public01_infra_deploy_rollback`, from `/opt/klai/logs/infra-deploy.log` via
public-01's Alloy (`service:infra-deploy`).

- `rolled_back`: the previous files serve again. Nothing is broken now. Fix the
  revision forward in klai-infra; the next revision deploys by itself.
- `rollback_failed`: the previous files failed the checks too. Treat the site,
  status page and Uptime Kuma as possibly down: `ssh` to public-01,
  `docker compose ps` in `/opt/klai/public` and `/opt/klai/alloy`, read
  `tail -n 20 /opt/klai/logs/infra-deploy.log` and `journalctl -t infra-deploy`.
  The `.prev` files sit next to the installed ones.
- Not rolled back: Uptime Kuma's own database after a Kuma image migration. The
  nightly backup (`docs/runbooks/uptime-kuma.md#backup-and-restore` in
  klai-infra) is the restore source, used by hand.

In VictoriaLogs:
`_time:1h service:infra-deploy | sort by (_time) desc | limit 10`.

## Public-01 infra deploy is silent

No heartbeat line (`event:infra_deploy` with outcome `deployed`, `up_to_date`
or `skipped_bad_rev`) reached VictoriaLogs for 2 hours
(`public01_infra_deploy_silent`); lines with outcome `failed` or
`validation_failed` are not counted. `deploy-infra.sh`
writes one every run, so silence is one of three things: the cron stopped, Alloy
stopped shipping `/opt/klai/logs/infra-deploy.log` (the Alloy container must
mount `/opt/klai/logs`), or every run fails before it finishes, which logs
`failed` or `validation_failed` with the failing `gate`. The three checks, in
order, are in the alert description.
