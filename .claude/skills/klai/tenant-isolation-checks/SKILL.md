---
name: klai-tenant-isolation-checks
description: |
  Klai tenant-isolation pattern checks. Codifies the standards from the
  audit-tenant-isolation-2026-05-05 fix cycle into reusable diff-time
  checks. Used by /klai:tenant-review and the GitHub Actions workflow.

  TRIGGER when reviewing a code diff that touches:
  - Postgres models with tenant columns (org_id, tenant_id)
  - Webhook or OAuth callback handlers
  - Service-to-service calls (klai-portal → knowledge-ingest, retrieval-api, etc.)
  - Qdrant search/scroll/upsert/delete
  - FalkorDB / Graphiti operations
  - Garage S3 image storage
  - Redis cache with tenant-scoped keys
  - Cross-org sites (lifespan, reapers, admin endpoints)
  - Pydantic Settings with secret/token fields
  - SOPS env-var changes

  NOT for: greenfield architecture decisions (use klai-security-audit),
  single-line typos, or non-Klai projects.
---

# Klai Tenant-Isolation Checks

This skill codifies the patterns from
`reports/audit-tenant-isolation-2026-05-05/standards.md` as a reviewable
checklist. Use it when reviewing a code diff to catch tenant-isolation
regressions BEFORE they ship.

## How to use

Given a diff (`git diff main` or PR diff), walk every changed line through
the relevant checks below. Each check has a hard-or-soft verdict:

- **HARD** — blocker, must be fixed before merge
- **SOFT** — flag for review, may be acceptable with explicit justification

Output format per finding:
```
[HARD|SOFT] file:line — <pattern violated>
  Current:    <code excerpt>
  Standard:   <link to standards.md section>
  Suggestion: <concrete fix>
```

## Check 1: Postgres RLS coverage (HARD)

For every new SQLAlchemy model with `org_id`/`tenant_id`/`customer_id`:
- [ ] Does an alembic migration create a Cat-D RLS policy on the table?
- [ ] Is `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` set?
- [ ] Is there an explicit `WITH CHECK` clause?
- [ ] Is the policy named `tenant_isolation` (or `_select` / `_insert` for split policies)?

For every new SQL `text()` query against an RLS-protected table:
- [ ] Does it include `WHERE org_id = ...` OR rely on RLS via `tenant_scoped_session()`?

For every new `op.create_table(...)` in alembic:
- [ ] If the table has `org_id`/`tenant_id`, is RLS added in the same migration?

**Standards ref:** standards.md sections 1, 2

## Check 2: Session-helper discipline (HARD)

For every new `AsyncSessionLocal()` direct usage (no helper):
- [ ] Is there a `# cross-org-by-design: <reason>` comment explaining why no helper?
- [ ] Does the code IMMEDIATELY call `set_tenant(db, org_id)` before any RLS query?
- [ ] If background task / poller: is `tenant_scoped_session(org_id)` or `cross_org_session()` used instead?

**HARD** — implicit cross-org via "no filter" is the bug class we're eliminating.

**Standards ref:** standards.md sections 3, 4

## Check 3: Cat-A `WITH CHECK` discipline (HARD)

For every new RLS policy on tables in the auth/login path (`portal_users`,
`portal_connectors`, `portal_join_requests`, etc.):
- [ ] Does USING include `OR current_setting(...) IS NULL` (Cat-A permissive read)?
- [ ] Does WITH CHECK have NO `OR IS NULL` branch (write must always bind a real org_id)?

Anti-pattern (Finding A-1): `FOR ALL` policy without explicit WITH CHECK
silently reuses USING — letting INSERTs land any org_id.

**Standards ref:** standards.md section 2

## Check 4: `_require_<X>_secret` validators (HARD)

For every new pydantic Settings field that is:
- A webhook secret (`*_webhook_secret`, `*_webhook_token`)
- A service-to-service token (`*_internal_secret`, `*_api_key`)
- An encryption key (`*_encryption_key`, `*_kek`)

Must have:
- [ ] `@model_validator(mode="after")` rejecting empty/whitespace
- [ ] Encryption keys: validator also checks base64-decodes to expected length
- [ ] Pre-flight: env-var exists in `klai-infra/core-01/.env.sops` BEFORE the validator merges
  (per `validator-env-parity` pitfall — comment in PR body confirming this)

**Standards ref:** standards.md section 5

## Check 5: Webhook handler composite (HARD)

For every new endpoint with `/webhook` or `/callback` in path:
- [ ] HMAC verification using `hmac.compare_digest` (NOT `==`)
- [ ] Validator on the secret field (Check 4)
- [ ] After HMAC verify, BEFORE side-effects: replay-check via `WebhookNonceStore`
- [ ] On `RedisUnavailableError`: HTTP 503 (fail-closed)
- [ ] On `NonceReplayError`: HTTP 409 (replay_blocked)
- [ ] Tenant resolution from VERIFIED payload (not URL path or unsigned body field)

**Standards ref:** standards.md sections 6, 15

## Check 6: Identity-assertion on internal endpoints (HARD)

For every new endpoint that:
- Reads `org_id` / `tenant_id` / `user_id` from request body OR query-param, AND
- Is auth-gated only by `INTERNAL_SECRET` middleware (not Zitadel JWT)

Must have:
- [ ] `klai_identity_assert.IdentityAsserter.verify(...)` call
- [ ] Caller-side: every consumer sends `X-Caller-Service: <known-name>` header
- [ ] Unit test that locks the header on outbound calls (per
      `retrieve-caller-service-header-mismatch` pitfall)

**Standards ref:** standards.md section 7

## Check 7: Qdrant filter-key discipline (HARD)

For every new `client.search/scroll/retrieve/delete/upsert` on Qdrant:
- [ ] Does the `Filter(must=[...])` include a `FieldCondition` for the
      collection's tenant key?
  - `klai_knowledge` → `org_id` (Zitadel string)
  - `klai_focus` → `tenant_id` (Zitadel string)  *(decommissioned per SPEC-DECOMM-FOCUS-001)*
- [ ] Type discipline: both are STRINGS (not int) in current code
- [ ] Cross-collection key-bug check: not `tenant_id` filter on `klai_knowledge`

**Standards ref:** standards.md section 11

## Check 8: FalkorDB / Graphiti per-org isolation (HARD)

For every new Cypher query OR Graphiti search:
- [ ] Goes through `client.select_graph(org_id)` (per-org physical graph), OR
- [ ] Has explicit `WHERE org_id = $1` / `WHERE n.group_id = $1`

**Standards ref:** standards.md section 12

## Check 9: Garage S3 access (SOFT after SPEC-TI-009 lands)

For every new Garage S3 read / write / presigned URL:
- [ ] Object key contains tenant prefix
- [ ] Read goes through portal-api auth-proxy (not anonymous Caddy → website-mode)
- [ ] If presigned-URL pattern: TTL ≤ 5 min

**Standards ref:** standards.md section 13

## Check 10: Redis tenant-prefixing (HARD)

For every new `redis.set/get/delete/scan/keys/lpush/...`:
- [ ] Key contains tenant component (`{namespace}:{zitadel_org_id}:...`)
- [ ] Producer and consumer use SAME shape (no int-vs-Zitadel-string fragmentation, per Finding B-5)
- [ ] Pub/sub channels: tenant-scoped or explicit cross-tenant comment

**Standards ref:** standards.md section 14

For every new tenant-scoped namespace:
- [ ] `_flush_redis_tenant_keys()` in deprovisioning_steps.py is extended to flush it (per Finding B-10)

## Check 11: Multi-org user resolution (HARD)

For every new `SELECT FROM portal_users WHERE zitadel_user_id = ...`:
- [ ] Includes `AND zitadel_org_id = :rid` from JWT resourceowner claim, OR
- [ ] Has explicit comment "no rid filter because: <pre-auth path / single-tenant service>"

Without rid filter, multi-org users get arbitrary tenant (Finding A-12).

**Standards ref:** standards.md section 10

## Check 12: Platform-admin gating (HARD)

For every new `app/api/admin/*.py` endpoint that takes a `slug` URL-param
that may identify a tenant DIFFERENT from the caller's own org:
- [ ] Calls `_require_platform_admin(_caller_org)` after `_require_admin(caller_user)`
- [ ] Logs the action via `log_event` to `portal_audit_log` with target slug + org_id

Without platform-admin gating, any tenant-admin can act on any other tenant
(Finding C-2).

**Standards ref:** standards.md section 16

## Check 13: Constant-time secret compare (HARD)

For every new comparison involving a secret/token/signature:
- [ ] Uses `hmac.compare_digest` (NOT `==` or `!=`)
- [ ] Operands are byte-encoded (`.encode("utf-8")`)

**Standards ref:** standards.md section 15, pitfall `non-constant-time-secret-compare`

## Check 14: post_deploy SQL operator-step (SOFT)

For every new alembic migration that:
- Creates RLS policies, OR
- Drops a table owned by `klai` (not `portal_api`)

The PR body MUST include the operator-step:
```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-portal/backend/alembic/versions/post_deploy_<rev>.sql
docker restart klai-core-<service>-1
```

**Standards ref:** standards.md section 8, pitfall `alembic-cannot-drop-non-portal_api-tables`

## Check 15: Auto-migrate via entrypoint.sh (HARD)

For every new alembic migration in services that DON'T currently auto-migrate
(klai-mailer, klai-knowledge-mcp):
- [ ] Either: add `entrypoint.sh` that runs `alembic upgrade head` before the CMD
- [ ] Or: explicit operator-step in PR body to run migration manually

Without this, the migration ships in the image but never applies on prod
(per `alembic-stamped-past-skipped-migration` pitfall).

Services that already have auto-migrate (verified 2026-05-05):
portal-api, klai-connector, scribe-api, klai-knowledge-ingest.

**Standards ref:** standards.md section 9

## Output template

When using this skill, structure the output as:

```markdown
# Tenant-Isolation Review — <branch>

**Diff scope:** `git diff main` (N files, M lines)

## HARD findings (block merge)

[None] OR
1. **Check N — file:line — <title>**
   - Current: ...
   - Standard: standards.md §<n>
   - Suggestion: ...

## SOFT findings (review)

[None] OR
1. ...

## Confidence

XX — <coverage of the diff, gaps>
```
