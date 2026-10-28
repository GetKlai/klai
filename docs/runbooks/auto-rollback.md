# Automatic rollback

Alerts: `auto_rollback_event`, `rollback_rehearsal_missing` (both
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
