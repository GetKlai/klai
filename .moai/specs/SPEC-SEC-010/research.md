# Research: SPEC-SEC-010 — Retrieval-API Hardening

## 0. Scope Note

This SPEC does not require new research. All analysis was done during the Phase 3 security audit on 2026-04-19 and is documented in `.moai/audit/`. This research document is a pointer map for the implementer — follow the audit documents for the full picture, and read the referenced reference implementations to mimic their patterns.

---

## 1. Summary of Findings Addressed

### F-001 — retrieval-api has zero authentication
**Source:** `.moai/audit/04-tenant-isolation.md` § F-001 (Fase 3.1)
**Severity:** CRITICAL (escalated from HIGH after PRE-B)
**Core evidence:**
- `klai-retrieval-api/retrieval_api/main.py:59` — only `RequestContextMiddleware`, no auth
- `klai-retrieval-api/retrieval_api/api/retrieve.py:55-56` — no `Depends(...)` on the route
- `klai-retrieval-api/retrieval_api/api/chat.py:22-23` — same
- `RetrieveRequest.org_id: str` (models.py:8-17) is trusted directly from the POST body
**Blast radius:** Any principal that reaches port 8040 can request chunks for any `org_id` they know. Combined with PRE-B (enumerable snowflake org_ids), this is CRITICAL.

### F-010 — No rate limit or request-size caps
**Source:** `.moai/audit/04-tenant-isolation.md` § F-010 (Fase 3.1)
**Severity:** LOW (individually) — combined with F-001 it amplifies enumeration attacks
**Core evidence:** `RetrieveRequest` in `models.py:8-17` has no `Field(...)` bounds
- `top_k: int = 8` — no upper cap
- `conversation_history: list[dict]` — no max_length
- `kb_slugs: list[str] | None` — no max_length
- `taxonomy_node_ids: list[int] | None` — no max_length

### F-014 — Body `user_id` is trusted → cross-user leak within tenant
**Source:** `.moai/audit/04-2-query-inventory.md` § F-014 (Fase 3.2/3.3)
**Severity:** HIGH (individually), CRITICAL combined
**Core evidence:**
- `klai-retrieval-api/retrieval_api/services/search.py:78-81` (`_scope_filter` for `scope="personal"`)
- `request.user_id` comes directly from the request body with zero verification
- Attack: attacker with legitimate access to tenant X as user A sets `body.user_id=<victim_user_B>` and reads B's personal chunks

### PRE-B result — Zitadel org_ids are enumerable
**Source:** `.moai/audit/04-3-prework-caddy.md` § PRE-B
**Evidence (sampled from live prod DB, read-only):**
```
1 | 362757920133283846 | 18 digits
8 | 368884765035593759 | 18 digits
```
- 18-digit snowflake-numerics (Zitadel v2+ scheme: timestamp-prefix + machine id + sequence)
- Active-org space for a multi-year-old platform: low thousands
- Enumeration cost at 60 ms per probe with no rate limit: ~100 k probes/hour = entire active range in minutes
- Conclusion: F-001 severity escalates HIGH → CRITICAL

### F-013 (positive) — Qdrant filter enforces org_id
**Source:** `.moai/audit/04-2-query-inventory.md` § F-013
**Why positive:** `klai-retrieval-api/retrieval_api/services/search.py:74-75` and `:122` already add `FieldCondition(key="org_id", match=MatchValue(value=request.org_id))` as a `must` filter. This is the final backstop and is NOT changed by this SPEC.
**Why not sufficient on its own:** F-014 still allows cross-user within the same org (user_id is also a must-filter but filtered on a caller-supplied value).

---

## 2. Reference Implementations

The implementer SHOULD read these files to mirror the patterns. They are NOT modified by this SPEC.

### `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py`
- Shape of InternalSecretMiddleware (ASGI middleware + `hmac.compare_digest`)
- **WARNING:** This file has the fail-open bug F-003 (SEC-011 will fix it). Do NOT copy the fail-open branch into retrieval-api. The audit-driven new middleware MUST fail closed on missing secret.

### `klai-focus/research-api/app/core/auth.py`
- Zitadel JWT validation via `python-jose.jwt.decode`
- JWKS retrieval and in-memory caching
- Audience and issuer verification flow
- Token claim extraction (`sub`, `resourceowner`)
- **NOTE:** This file has the F-004 bug (audience verification is opt-in). SEC-012 will make audience mandatory in research-api. For retrieval-api, this SPEC starts mandatory from day one.

### `klai-portal/backend/app/services/partner_rate_limit.py` (and `partner_dependencies.py`)
- Redis sliding-window pattern: ZADD + ZREMRANGEBYSCORE + ZCARD in a pipeline
- `Retry-After` header computation
- Fail-open behavior when Redis is unreachable
- Key-derivation pattern (per-key identity)

### `klai-connector/app/middleware/auth.py`
- Combined Zitadel introspection + portal bypass-secret pattern
- Example of how to wrap a FastAPI `BaseHTTPMiddleware`
- **NOTE:** F-009 / F-011 apply (non-constant-time compare, TTL cache issue). Do NOT copy those bugs.

---

## 3. Related Existing Implementations

### portal-api RequestContext + tenant isolation
- `klai-portal/backend/app/api/dependencies.py:43 _get_caller_org` (@MX:ANCHOR fan_in=8) — the gold-standard pattern for resolving a Zitadel token → `PortalOrg` + `PortalUser` + RLS session binding
- `klai-portal/backend/app/core/database.py:51 set_tenant()` — RLS session var
- Demonstrates the "internal service trust model" that justifies REQ-3.3's internal-secret bypass of cross-user/org checks: portal-api has already done the work.

### Caller implementations
- `klai-portal/backend/app/services/partner_chat.py:84 retrieve_context()` — where TASK-009 adds the new header
- `klai-focus/research-api/app/services/retrieval_client.py` — `retrieve_broad()` and `retrieve_narrow()` — TASK-010 target
- `deploy/litellm/klai_knowledge.py` — LiteLLM hook; TASK-010 target (or the hook-config YAML, whichever defines the retrieval-api call)

---

## 4. Open Questions Resolved During Audit

| Question | Answer | Source |
|---|---|---|
| Does `portal_api` DB role have `BYPASSRLS`? | No — `bypassrls=false` | `.moai/audit/04-3-prework-caddy.md` § PRE-A |
| Are Zitadel org_ids enumerable? | Yes — 18-digit snowflake numerics | § PRE-B |
| Is retrieval-api exposed publicly via Caddy? | No — Docker-intern only (port 8040) | § Caddy verify table |
| Does Qdrant filter on `org_id`? | Yes — as `must` filter (F-013 positive) | `.moai/audit/04-2-query-inventory.md` § F-013 |
| Who calls retrieval-api today? | portal-api partner_chat, focus retrieval_client, LiteLLM hook | `.moai/audit/04-tenant-isolation.md` § A.2 |

---

## 5. Open Items NOT Addressed Here (future SPECs or parking lot)

- `kb_slugs` format guessability — parking lot in `.moai/audit/04-2-query-inventory.md`; may become its own SEC SPEC
- Replacing shared `INTERNAL_SECRET` with mTLS — deferred
- SEC-011 (SEC-011 in roadmap) — knowledge-ingest fail-closed auth (F-003 + F-012)
- SEC-012 — JWT audience verification mandatory in research-api and scribe-api (F-002 + F-004)
- SEC-004 — Defense-in-depth auth middleware for focus + scribe (F-005, F-006, F-009)
- SEC-008 — Caddy exposure hardening (F-017 connector public, F-018 dev public, F-020, F-022)

All are tracked in `.moai/audit/99-fix-roadmap.md`.

---

## 6. Why No New Research Is Needed

The audit phase already produced:
- Concrete file+line references for every finding
- Reference implementations to mimic
- A verified understanding of the caller graph (A.2 call-chain appendix)
- Verified assumptions (PRE-A, PRE-B, Caddy verify)
- A prioritized roadmap that places SEC-010 as P0 CRITICAL with a small, well-scoped fix

Starting the implementation requires reading the three reference files (§ 2 above), the three caller files (§ 3), and acting on the requirements in `spec.md`. No further investigation is needed before writing code.

If during implementation a new edge case surfaces (e.g. the `role` claim shape from Zitadel differs from what research-api assumes, or JWKS caching semantics need tuning), capture it in a `## Changelog` entry in spec.md with `v0.1.X` and add the note here. Do NOT let the SPEC drift silently.
