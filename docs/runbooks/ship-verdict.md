# Ship verdict: is a merged commit live and proven?

`scripts/ship-verdict.sh <sha-or-pr-number>` follows the deploy runs of one commit on main and the E2E prod-tenant run after them, and ends with one line `verdict=<...>`. It replaces polling for "anything deploy-like still running" with fixed sleeps. It needs `gh` and `jq` only, and only reads.

- The deploy workflows are the `workflow_run.workflows` list of `e2e-prod-tenant.yml` at that commit, plus the LibreChat config sync (which triggers no E2E run).
- `live-and-proven` (exit 0): the first E2E run on main whose job started after the last deploy run finished, any event, ended green. Cancelled and skipped runs do not count. E2E has no link to the commit, so the output names main's tip at that run.
- `nothing-to-deploy` (0): no deploy workflow started within `--grace` (120 s). `failed`, `failed-e2e`, `rolled-back` (1): output names workflow, job and step, or the Auto-rollback run that uploaded `rollback-report`. `timeout` (2): `--timeout` (2700 s). `pending` and `deployed-no-e2e` (3): `--no-wait`, or only the config sync ran; start E2E by hand or wait for the nightly run. `error` (4): bad input, or the commit is not on main.
- It first reports when main was already red before the merge, with the open `ci-failure` issue. `--poll` sets the interval (30 s).

Ceiling: a green E2E run after the deploys proves the commit only as far as that suite covers; completion times come from run `updatedAt`.
