# LiteLLM Provider Auth Failed

Alerts: `litellm_provider_auth_failed`, `mistral_workspace_unavailable`,
`mistral_no_usable_key`, `mistral_api_probe_missing`

## Meaning

LiteLLM logged an upstream provider authentication failure. User-facing chat or
search synthesis can fail even when `litellm` and `retrieval-api` health checks
are green.

For Mistral errors like:

```text
litellm.AuthenticationError: MistralException - {"detail":"Unauthorized"}
```

the deployed `MISTRAL_API_KEY` is missing, invalid, expired, revoked, or not
present in the running LiteLLM container environment.

## Confirm

In Grafana Explore / VictoriaLogs:

```text
_time:15m service:litellm
  ("AuthenticationError" OR "MistralException" OR "Unauthorized")
| sort by(_time) desc
| limit 20
```

Check the model alias and provider in the log body. In the current config,
`klai-primary`, `klai-fast`, `klai-large`, and `klai-medium` each have an
`order: 1` deployment on `MISTRAL_API_KEY` and an `order: 2` deployment on
`MISTRAL_API_KEY_2`. LiteLLM only moves an alias to order 2 when order 1 is
full (HTTP 402), never on a 401, so a bad `MISTRAL_API_KEY` is not recovered
by the second workspace. `klai-ingest` and `klai-judge` use
`MISTRAL_VIBE_KEY` and have no second workspace.

Also check the synthetic Mistral API probe:

```text
_time:15m service:mistral-api-probe event:mistral_api_probe
| sort by(_time) desc
| limit 20
```

It logs one line per key per run (every 2 minutes) with `key_env`,
`key_fingerprint` (first 12 hex chars of sha256 of the key, never the key),
`status` and `error`:

| status | http_status | meaning |
|---|---|---|
| `ok` | 200 | the key completed a one-token request |
| `full` | 402 | the workspace's monthly spending limit is reached |
| `error` | 401 | Mistral rejected the key before LiteLLM was involved |
| `error` | other, or 0 | 5xx, timeout or no connection from core-01 |

## Mistral key state

`mistral_workspace_unavailable` (warning, one alert per key) fires while the
latest probe runs see a key `full` or `error`. `mistral_no_usable_key`
(critical) fires when no `MISTRAL_API_KEY*` key was `ok` in one run (any mix
of `full` and `error` counts, as does `error=env_file_unreadable`, where the
probe could not read `/opt/klai/.env`), so the chat aliases have no working
workspace left. Both route to
`klai-ops-alerts-email` and repeat every hour until resolved.

The probe also writes the per-key state to
`/opt/klai/state/litellm/mistral-keys.json`, mounted read-only into the
`litellm` container as `/app/state/mistral-keys.json`. The
`klai_mistral_pool` hook never offers a key that file calls `full`, so while
one workspace is full no user request has to fail on it first. A 402 on a real
request benches the key at once as well, until a later probe run says `ok`.

```bash
cat /opt/klai/state/litellm/mistral-keys.json
# {"generated_at": "...", "keys": {"<fingerprint>": {"state": "full", "since": "...", "http_status": 402}, ...}}
```

To see which `key_env` a fingerprint belongs to, read the probe log line,
not the env file. The hook logs every switch as
`klai_mistral_pool_state_change key_fingerprint=<fp> state=<full|ok> source=<probe|request>`:

```text
_time:24h service:litellm "klai_mistral_pool_state_change"
```

Recovering a full workspace: raise its spending limit or enable
pay-as-you-go in the Mistral console. Nothing has to be restarted; the next
probe run (at most 2 minutes) marks the key `ok`, LiteLLM picks it up within
10 seconds of that, and the alert resolves within 5 minutes.

`mistral_api_probe_missing` (warning) means no probe line for 10 minutes.
Once the state file is older than 10 minutes LiteLLM stops trusting it and
falls back to learning about full keys from failed user requests (a 402
benches a key for 5, then 15, then 60 minutes). Check
`systemctl status mistral-api-probe.timer mistral-api-probe.service`,
`/opt/klai/logs/mistral-api-probe.log`, Alloy and VictoriaLogs.

## Probe unit

`deploy-compose.yml` syncs the script to `/opt/klai/scripts/` and stages the
unit and timer in `/opt/klai/systemd/`, but it runs no `systemctl` (no sudo).
Activating them, or picking up a changed unit file, is manual:

```bash
ssh core-01
sudo ln -sf /opt/klai/systemd/mistral-api-probe.service /etc/systemd/system/mistral-api-probe.service
sudo ln -sf /opt/klai/systemd/mistral-api-probe.timer   /etc/systemd/system/mistral-api-probe.timer
sudo systemctl daemon-reload
sudo systemctl enable --now mistral-api-probe.timer
systemctl show mistral-api-probe.service -p TimeoutStartUSec   # expect 1min
```

With the symlinks in place a later unit change still needs the
`daemon-reload`. A run with two keys takes at most 26 s, so it also fits the
old 30 s timeout until then.

## Recover

1. Verify the deployed secret source contains a valid `MISTRAL_API_KEY`.
2. Verify the resolved LiteLLM container environment without printing the value:

   ```bash
   docker compose config litellm | grep -A 30 'environment:'
   ```

3. Recreate LiteLLM so the corrected environment is loaded:

   ```bash
   /opt/klai/scripts/compose-up.sh --force-recreate litellm
   ```

4. Confirm the error stopped:

   ```text
   _time:5m service:litellm
     ("AuthenticationError" OR "MistralException" OR "Unauthorized")
   | stats count() as n
   ```

5. Run a real chat/search turn and verify answer synthesis succeeds.

## Related Files

- `deploy/litellm/config.yaml`
- `deploy/litellm/klai_mistral_pool.py`
- `deploy/docker-compose.yml`
- `scripts/mistral-api-probe.sh`
- `deploy/systemd/mistral-api-probe.service`, `deploy/systemd/mistral-api-probe.timer`
- `deploy/grafana/provisioning/alerting/litellm-rules.yaml`
- `deploy/grafana/provisioning/alerting/mistral-api-rules.yaml`
