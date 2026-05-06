# SPEC-TI-010B-FOLLOWUP-001 — Redis tenant-key namespace consistency

**Predecessor:** PR #381 (closed, branch `feature/SPEC-TI-010B-REDIS`) — adversarial audit
**Priority:** HIGH (GDPR-relevant — retrieval-log query content survives deprovisioning)
**Status:** Ready

## Goal

Pick ONE namespace convention for tenant-scoped Redis keys and apply it
consistently across writers and the deprovisioning sweep. Today the
writer for `rl:` (retrieval-log) and `templates_rl:` (rate-limit) uses
`PortalOrg.id` (int PK), while `deprovisioning_steps.py` SCANs/DELs
using `state.zitadel_org_id` (string Zitadel resourceowner). The
namespaces never overlap, so deprovisioning never deletes the actual
keys. Retrieval-log entries — containing `chunk_ids`,
`query_resolved`, and `reranker_scores` for a deleted tenant — survive
in Redis until their TTL expires (0–60 minutes).

The chosen convention is `zitadel_org_id` (string), matching the
already-consistent `templates:`, `kb_ver:`, and `kb_feature:` keys and
LiteLLM's metadata. This is also resilient to `PortalOrg.id` reuse if
an org is ever recreated with the same int PK.

## Acceptance criteria

- **AC-1** `klai-portal/backend/app/services/retrieval_log.py:46`
  writes keys as `f"rl:{zitadel_org_id}:{user_id}"`. The writer
  function signature accepts `zitadel_org_id: str` (not `org_id: int`).
  All callers are updated to pass the Zitadel resourceowner.

- **AC-2** `klai-portal/backend/app/api/app_templates.py:110` calls
  `check_rate_limit(pool, f"templates_rl:{org.zitadel_org_id}", ...)`
  using the string Zitadel resourceowner.

- **AC-3** `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:222,227`
  patterns (`f"rl:{zid}:*"`, `f"templates_rl:{zid}"`) are unchanged —
  these were always correct against `state.zitadel_org_id`. The fix
  is on the writer side.

- **AC-4** A regression test in
  `klai-portal/backend/tests/test_retrieval_log_keying.py` asserts:
  - `retrieval_log` writer SETs a key matching
    `rl:<zitadel_org_id>:<user_id>` against a fakeredis or real Redis
    fixture.
  - `app_templates` rate-limit writer SETs a key matching
    `templates_rl:<zitadel_org_id>`.
  - Neither writer produces a key prefixed with the int `org.id`.

- **AC-5** A regression test in
  `klai-portal/backend/tests/test_deprovisioning_redis_sweep.py`:
  - Seeds synthetic keys `rl:362757920133283846:user-x` and
    `templates_rl:362757920133283846`.
  - Invokes the deprovisioning Redis-sweep step with
    `state.zitadel_org_id = "362757920133283846"`.
  - Asserts both keys are deleted post-sweep.

- **AC-6** `_invalidate_litellm_kb_cache` in
  `klai-portal/backend/app/api/app_account.py:41-47` is refactored to
  use `get_redis_pool()` instead of constructing a bespoke
  `aioredis.Redis(host=..., port=..., password=...)` instance. Lower
  priority — ship in a separate commit if it complicates AC-1..AC-5.

## Background

Writer (today, wrong):

```python
# retrieval_log.py:46
key = f"rl:{org_id}:{user_id}"          # org_id: int, PortalOrg.id PK

# app_templates.py:110
check_rate_limit(pool, f"templates_rl:{org_id}", ...)  # org.id (int)
```

Deprovisioning (today, correct shape but never matches):

```python
# deprovisioning_steps.py:222,227
f"rl:{zid}:*"                            # zid = state.zitadel_org_id (str)
f"templates_rl:{zid}"
```

Because `org.id` (e.g. `42`) and `zitadel_org_id` (e.g.
`"362757920133283846"`) are different namespaces, the deprovisioning
SCAN never matches the writer's keys. Retrieval-log entries persist
until their per-key TTL elapses; rate-limit keys auto-expire in ~1
minute and are less critical, but the same audit gap evidences the
B-10 audit pass was incomplete.

The `templates:`, `kb_ver:`, and `kb_feature:` keys already use
`zitadel_org_id` consistently across writer and consumer paths, so
aligning `rl:` and `templates_rl:` on the same convention is the
lower-risk choice and matches LiteLLM's metadata.

## Operator step (after merge)

No SQL or out-of-band action. Ship the code change, deploy
portal-api, then monitor:

```
service:portal-api AND message:"retrieval_log" AND level:error
service:portal-api AND message:"deprovisioning" AND level:error
```

Existing `rl:<int>:*` keys in Redis from the pre-fix writer will
expire on their own TTL (max 60 minutes) and will not be touched by
the new sweep. Operator may optionally purge them with a one-shot
`SCAN 0 MATCH 'rl:*'` filtered to numeric-only first segment, but
this is not required.

## Out of scope

- Connector wiring bugs in `sync_engine.py` / `scheduler.py` —
  separate SPEC: `SPEC-TI-002-FOLLOWUP-001`.
- Redis URL password parsing concerns — already covered by the
  `redis-url-password-must-be-parsed-manually` pitfall.
- Migration of `templates:` / `kb_ver:` / `kb_feature:` keys — these
  already use `zitadel_org_id` and are correct.
- TTL changes on retrieval-log entries.
