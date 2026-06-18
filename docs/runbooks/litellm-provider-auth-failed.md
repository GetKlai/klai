# LiteLLM Provider Auth Failed

Alert: `litellm_provider_auth_failed`

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
`klai-primary`, `klai-fast`, `klai-large`, and `klai-medium` all use:

```yaml
api_key: os.environ/MISTRAL_API_KEY
```

So fallback between these aliases does not recover from a bad Mistral key.

Also check the synthetic Mistral API probe:

```text
_time:15m service:mistral-api-probe event:mistral_api_probe
| sort by(_time) desc
| limit 20
```

`status=fail` with `http_status=401` means Mistral rejected the deployed key
before LiteLLM was involved. Check the workspace monthly spending limit/API
paused state as well as the key itself.

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
- `deploy/docker-compose.yml`
- `deploy/grafana/provisioning/alerting/litellm-rules.yaml`
