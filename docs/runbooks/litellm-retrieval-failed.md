# LiteLLM knowledge-hook retrieval failed

**Alert:** `obs-001-litellm-kb-retrieval-failed` (CRIT)
**Source:** `deploy/grafana/provisioning/alerting/litellm-rules.yaml`
**Symptom:** at least one `KlaiKnowledgeHook: retrieval` log line in the
last 5 min on `service:litellm`. Every fired log line means a chat
answered without KB context — the user's "Chat met: <collection>" UI
is silently lying.

## What happens when this fires

The LiteLLM `klai_knowledge_hook` calls retrieval-api on every chat
message to inject KB chunks into the system prompt. When the call
fails, the hook (post-2026-05-05 fail-loud rewrite) prepends a notice
to the system prompt instructing the model to start its reply with
"Let op: ik kon de kennisbank niet bereiken (...)". The user sees a
plain-language warning instead of a coherent answer that secretly
omits the KB.

A single failure pages because every failed call = a real user got a
worse answer than they expected.

## Triage

1. **Identify the failure mode.** In Grafana → Explore → VictoriaLogs:

   ```
   _time:15m service:litellm "KlaiKnowledgeHook: retrieval"
   ```

   Inspect the message body. Most common modes:

   | Symptom | Likely cause | Fix |
   |---|---|---|
   | `HTTP 400` body contains `missing_caller_service` | Caller stopped sending `X-Caller-Service` header (or a new caller was added without it). | See pitfalls → `retrieve-caller-service-header-mismatch`. Add the header in the caller; redeploy. |
   | `HTTP 400` body contains `unknown_caller_service` | Caller sent a `X-Caller-Service` value not in `klai_identity_assert.KNOWN_CALLER_SERVICES`. | Add the new service name to the lib + portal-api `identity_verifier.KNOWN_CALLER_SERVICES`. The contract test in portal-api will lock the two lists together. |
   | `HTTP 401` `invalid_jwt_audience` | Zitadel project / audience config drifted (Phase C-1 JWT path). Hook falls back to legacy `X-Internal-Secret` automatically. | If the fallback also fails: Zitadel project for retrieval-api is misconfigured. Re-check `KLAI_LITELLM_CLIENT_*` in SOPS. |
   | `HTTP 401` `invalid_internal_secret` | `RETRIEVAL_API_INTERNAL_SECRET` divergence between caller and receiver. | Compare SOPS env vars on both ends. Rotate secret if needed. |
   | `ConnectError` / `TimeoutError` | retrieval-api is down or networking blip. | Check `service:retrieval-api` health, `docker ps`, and the rate-limit / Redis pool errors (separate latent bug). |

2. **Confirm impact.** Run:

   ```
   _time:15m service:litellm "KB injection"
   ```

   If this returns zero hits, the hook has been broken across the
   whole window — every chat in that window has degraded. Tell the
   on-call / customer-success rep so support can triage tickets.

3. **Check for regression PRs.** Recent merges that touched any of:
   - `deploy/litellm/klai_knowledge.py`
   - `klai-libs/identity-assert/`
   - `klai-retrieval-api/retrieval_api/middleware/auth.py`
   - any caller (`partner_chat.py`, `gap_rescorer.py`,
     `klai-focus/research-api/app/services/retrieval_client.py`)

   Bisect with `git log --oneline --since='1 day ago'` on the affected
   service.

## Resolution

- **Caller missing header**: PR fix following the same pattern as PR
  #311 (2026-05-05). Add `X-Caller-Service: <name>` to the outbound
  call AND register `<name>` in BOTH allowlists (lib + portal-api).
- **Receiver allowlist drift**: add the new caller name to
  `klai-libs/identity-assert/.../models.py` `KNOWN_CALLER_SERVICES`
  AND `klai-portal/backend/app/services/identity_verifier.py`
  `KNOWN_CALLER_SERVICES` in the same PR. The
  `test_library_and_server_caller_allowlists_match` portal-api test
  blocks merges that update only one side.
- **Network blip**: usually self-healing. If retrieval-api is in a
  restart loop, `docker logs klai-core-retrieval-api-1 --tail 100` and
  fix the underlying cause (env var, image version, port).

## Excluded from this alert by design

The log line `KlaiKnowledgeHook: jwt rejected by receiver (HTTP 401) —
retrying with legacy auth header` is filtered out of the LogsQL query.
It is the EXPECTED Phase C-1 fallback path while Zitadel audience
config is rolled out per-receiver. The actual outcome (success or
failure of the legacy retry) is logged separately and IS caught by
this alert.

## Background

This alert was added on 2026-05-05 after a 7-day silent outage caused
by SPEC-SEC-IDENTITY-ASSERT-001 Phase D (landed 2026-04-28). The
receiver gained a mandatory header check; none of the four in-repo
callers were updated; every chat ran without KB context for a week.
The hook caught the failure as a `warning` and degraded silently with
no alert. After the fix, the failure log is at `error` level AND a
user-visible notice is prepended to the system prompt AND this alert
fires immediately.

See pitfalls/process-rules.md → `retrieve-caller-service-header-mismatch`.
