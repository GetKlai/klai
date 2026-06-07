# 2026-05-05 — `X-Caller-Service` header mismatch silently killed KB retrieval for 7 days

**Pitfall (now live in):**
- `.claude/rules/klai/pitfalls/process-rules.md` § `retrieve-caller-service-header-mismatch (CRIT)`

**Severity:** HIGH — every Klai chat answer for ~7 days was generated **without
Kennisbank context** (general-knowledge only). No data loss, no crash, no error
shown to users. The failure was invisible because every caller degraded
fail-open. This retro is the runbook the `litellm_retrieve_hook_failing` alert
(SPEC-LAUNCH-SOFTLAUNCH-001) links to.

**SPEC / PRs involved:**
- SPEC-SEC-IDENTITY-ASSERT-001 Phase D — landed 2026-04-28, made
  `X-Caller-Service` a **required** header on `retrieval-api /retrieve`.
- Hotfix (2026-05-05) — added the header to all four callers, added allowlist
  tests, switched the LiteLLM hook from fail-open to fail-loud, and added this
  alert.

## What happened

SPEC-SEC-IDENTITY-ASSERT-001 Phase D hardened the internal `/retrieve`
endpoint: it began rejecting any request that did not send
`X-Caller-Service: <known-service>` with a `400`. The SPEC PR updated the
**receiver** (`klai-retrieval-api`) and the receiver's own tests — but it did
**not** update the four in-repo callers that POST to `/retrieve`:

| Caller | File | User-visible symptom |
|---|---|---|
| LiteLLM hook (path A — LibreChat chat) | `deploy/litellm/klai_knowledge.py` | Every chat answered with no KB context for 7 days |
| Partner API | `klai-portal/backend/app/services/partner_chat.py` | Partner `/chat/completions` returned no KB chunks |
| Gap re-scorer (background) | `klai-portal/backend/app/services/gap_rescorer.py` | Job silently `400`'d on every call |
| Focus narrow retrieval | `klai-focus/research-api/app/services/retrieval_client.py` | Notebook narrow returned `[]` |

From 2026-04-28 the receiver returned `400` to all four. The chats still
produced fluent, plausible answers — just from the model's general knowledge,
not from the customer's Kennisbank.

## Root cause

A contract change (new mandatory header) shipped on the receiver without
updating its callers in the same PR. The callers live in directories the SPEC
author did not normally edit (including another service, `klai-focus`), so a
receiver-only review missed them.

## Why nobody noticed for a week

Every caller wrapped the `/retrieve` call in a fail-open guard of the shape
`except Exception → log.warning → return empty/no-context`. So:

- No 5xx, no user-facing error — the chat UI looked healthy.
- The degradation (answers without KB) is exactly what a low-relevance query
  also looks like, so it didn't stand out.
- There was no alert on retrieval-failure rate.

It was discovered only when a user asked *"is the KB even being queried?"* and
someone tailed the LiteLLM logs and saw `400` on every `/retrieve`.

## The fix (2026-05-05 hotfix)

1. Added `X-Caller-Service: <name>` to all four outbound `/retrieve` calls.
2. Added an **allowlist test per caller** that mocks the httpx client and
   asserts the header is set — so the next refactor that drops it fails CI.
3. Bumped the LiteLLM hook from `warning → error` on any `/retrieve` failure
   **and** injected a user-visible
   `[Klai Kennisbank — TIJDELIJK NIET BEREIKBAAR]` notice into the system
   prompt, so silent-degrade becomes loud-degrade.
4. Added this alert: `service:litellm AND level:error AND _msg:"retrieval"`
   firing on any occurrence in 15m.

## If the `litellm_retrieve_hook_failing` alert fires now

The alert means the LiteLLM hook (`klai_knowledge.py`) failed to fetch KB
context in the last 15 minutes — users are getting no-KB answers right now.

1. **Confirm + identify the failure mode** (Grafana → Explore → VictoriaLogs):
   ```
   service:litellm AND level:error AND _msg:"retrieval" | sort by(_time) desc | limit 20
   ```
   Look at the status code and reason on the failing `/retrieve` call.
2. **`400` with an identity/caller reason** → a caller dropped or changed
   `X-Caller-Service`, or the receiver's known-caller allowlist
   (`klai_identity_assert.KNOWN_CALLER_SERVICES`) no longer contains the
   caller's name. This is the exact regression above — check recent deploys to
   `deploy/litellm/klai_knowledge.py` or `klai-retrieval-api`.
3. **`401` / identity-verify failure** → see the internal-secret /
   identity-assert path, not this header. Check `PORTAL_INTERNAL_SECRET`
   parity between LiteLLM and portal-api.
4. **5xx / timeout** → `retrieval-api` itself is unhealthy:
   ```
   ssh core-01 "docker logs --tail 100 klai-core-retrieval-api-1"
   ```
   and check its upstreams (Postgres, embeddings, FalkorDB).
5. **Cross-repo audit when a `/retrieve` contract changes** — grep every
   caller in the monorepo before merging:
   ```
   grep -rn '/retrieve\|RETRIEVE_URL\|knowledge_retrieve_url' \
       --include='*.py' --include='*.ts' .
   ```
   Patch every match in the same PR, or gate the receiver change behind a
   per-caller allowlist for the soak window.

## Prevention

- **Allowlist tests** lock in `X-Caller-Service` on each caller's outbound
  call. Keep them.
- **Receiver-side contract test** in `klai-retrieval-api` POSTs with the exact
  header set every caller sends.
- **Fail-loud on retrieval failure** (done for the LiteLLM hook): silent
  degrade on a feature the user believes they have is worse than a loud error.
- **This alert** surfaces the failure within 15 minutes instead of 7 days.
