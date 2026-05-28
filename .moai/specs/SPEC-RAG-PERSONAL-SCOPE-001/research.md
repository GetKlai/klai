# Research: Server-side enforcement of Persoonlijk-KB narrowing in retrieval-api

> Date: 2026-05-27
> Authors: Mark Vletter + Claude Opus 4.7
> Status: complete
> SPEC: SPEC-RAG-PERSONAL-SCOPE-001

## 1. Incident timeline

### 1.1 The Jantine bug (2026-05-27 17:00 CEST)

Jantine (GetKlai org, admin role) opens a chat with **"Persoonlijk"** selected as the only knowledge source. Her widget UI lists exactly one canonical Persoonlijk-KB (kb_id=24, slug=`personal-300000000000000002`, source = `jantinedoornbos.nl`). She asks *"wie is jantine?"* and gets chunks back from a **separate** user-created KB called `test2` (kb_id=33), containing data from her own OneDrive `company.csv`. The `test2` KB was explicitly unchecked elsewhere in the dropdown — visually deselected.

PR #705 (merged 17:36 CEST) made the LiteLLM-hook send `kb_slugs=["personal-{user_id}"]` together with `scope=personal`. Retrieval-api's `_scope_filter` honours the kb_slug filter for admin/company/kb_manager/group_manager users, so Jantine's chat surface stopped leaking immediately.

### 1.2 The follow-up gap (this SPEC)

While investigating "is this code safe enough?" we read `klai-retrieval-api/retrieval_api/api/retrieve.py` lines 258-262 and found:

```python
if req.effective_role == "personal":
    if req.scope != "personal":
        req = req.model_copy(update={"scope": "personal", "kb_slugs": None})
    elif req.kb_slugs is not None:
        req = req.model_copy(update={"kb_slugs": None})
```

For users whose `effective_role == "personal"`, retrieval-api **strips the kb_slugs field** before search. The strip is RBAC-driven (SPEC-PORTAL-RBAC-REFACTOR-001 REQ-17) — its intent is "personal-role callers cannot reach ORG KBs by spec'ing org-side kb_slugs". The implementation is over-broad: it also strips the defensive personal-canonical filter we just shipped.

Result: for personal-role users (= the default role for every newly-onboarded employee; SPEC-PORTAL-RBAC-REFACTOR-001 REQ-11 says *"New users join as personal"*), the Jantine-bug is **still live in production after PR #705 / #715 / #716**.

## 2. Architecture walkthrough

### 2.1 Caller inventory for `/retrieve`

Codebase-wide grep on 2026-05-27:

| Service | File | `scope=` used | `kb_slugs=` shape |
|---|---|---|---|
| LiteLLM hook (path A) | `deploy/litellm/klai_knowledge.py:1989` | `personal` (when dropdown = Persoonlijk-alone), `both` (Persoonlijk + org KBs), `org` (org KBs only) | `[personal-<user_id>]` or `[org_slugs]` or `[mixed]` |
| Knowledge-mcp | `klai-knowledge-mcp/main.py:1080` | `both` | not sent (None) |
| Partner-chat (path B) | `klai-portal/backend/app/services/partner_chat.py:1369` | `org` | optional org slugs |
| Internal/Research | none | n/a | n/a |

**Key conclusion**: the only caller that sends `scope=personal` is the LiteLLM-hook. knowledge-mcp uses `scope=both` for "all my chunks" semantic (third-party MCP); partner_chat uses `scope=org` for widget chat.

### 2.2 Current `_scope_filter` behaviour

`klai-retrieval-api/retrieval_api/services/search.py:69-115`:

For `scope == "personal"`:
- Add `org_id = X` filter
- Add `user_id = U` filter (if user_id present)
- **No visibility filter** — comment says *"personal scope is already restricted to one user; no visibility filter needed"*
- If `kb_slugs` is set after RBAC strip: add `kb_slug IN [list]` filter

For `scope == "org"` / `scope == "both"`:
- Add `org_id = X` filter
- Add visibility filter: `visibility != private` OR (`visibility = private` AND `user_id = U`)
- If `kb_slugs`: for `scope = both` add `(kb_slug IN [list] OR user_id = U)` — for `scope = org` add `kb_slug IN [list]`

**Two leak shapes**:

1. **scope=personal + RBAC-stripped kb_slugs**: returns every chunk where `user_id = U`. Personal-role users hit this path.
2. **scope=both** (any role): the visibility-should branch unconditionally lets `user_id = U` chunks pass — even when kb_slugs explicitly listed only `[org1, org2]`. The user's `test2` KB chunks come through.

The Jantine incident was shape (1). Shape (2) is silently present today but no one has reported it yet — the UI dropdown only triggers it when a user picks `Persoonlijk + org-KBs` and ALSO has unwanted user-created private KBs.

### 2.3 Server-side validation already in place

`klai-retrieval-api/retrieval_api/api/retrieve.py:252-253`:

```python
if req.scope in ("personal", "both") and not req.user_id:
    raise HTTPException(status_code=400, detail="user_id required for scope=personal/both")
```

The earlier worry that "scope=personal without user_id returns the whole org" is **already mitigated** by this 400. The remaining gap is purely the kb_slug-narrowing one.

### 2.4 Slug-template ownership

`klai-portal/backend/app/services/default_knowledge_bases.py:22-24`:

```python
def personal_kb_slug(user_id: str) -> str:
    """Build the canonical personal KB slug for a user."""
    return f"personal-{user_id}"
```

This helper is also used during provisioning (`create_default_personal_kb`, `ensure_default_knowledge_bases`) and as the magic-slug shortcut in `get_kb_with_access`. It is the single source of truth on the portal-api side.

After PR #716, the canonical slug is also surfaced to the LiteLLM-hook via `KnowledgeFeatureResponse.personal_kb_slug`. The hook in PR #715 builds the kb_slug filter from that value.

## 3. Trade-off analysis

Four design options surfaced during research. We weigh each against (a) drift risk vs the slug template, (b) deploy-window risk, (c) defense-in-depth value, (d) refactor cost.

### Option A: Bigger client-side wallpaper

Frontend dropdown sends `kb_slugs=[personal-<user>, ...other-selected]` directly, with `scope` reduced to a pure visibility hint. The current `kb_personal_enabled` boolean disappears in favour of explicit slug listing.

| Dimension | Score |
|---|---|
| Drift risk | High — slug template now lives in frontend too |
| Deploy risk | High — UI + portal-api + hook must ship together |
| Defense-in-depth | Low — still client-driven; server trusts the list |
| Refactor cost | Large — every chat surface needs UI changes |

**Verdict**: rejected. Maintains the same trust model that produced this bug; adds drift surface.

### Option B: Explicit `personal_kb_slug` field on RetrieveRequest

Add `personal_kb_slug: str | None` to RetrieveRequest. When `scope=personal` and field present, retrieval-api forces `kb_slug = personal_kb_slug`. Hook propagates field from portal feature response (already present per PR #716).

| Dimension | Score |
|---|---|
| Drift risk | Low — portal owns template, retrieval-api receives the slug |
| Deploy risk | Low — additive field; old hooks fall through to current behaviour |
| Defense-in-depth | Medium — server enforces only what client sent |
| Refactor cost | Small — 1 field, 2 services |

**Verdict**: viable. But the server still trusts the client to send the right slug; a buggy or malicious client that omits or scrambles the field bypasses the narrowing.

### Option C: Server-derived canonical slug via shared library

Retrieval-api derives `canonical_slug = personal_kb_slug(verified_user_id)` using a new shared library (`klai-libs/kb-slugs`) that BOTH portal-api and retrieval-api import. The verified_user_id comes from `verify_body_identity` — already enforced server-side per SPEC-SEC-IDENTITY-ASSERT-001.

`_scope_filter` for `scope=personal`:
```python
conditions.append(FieldCondition(key="kb_slug", match=MatchValue(value=canonical_slug)))
conditions.append(FieldCondition(key="user_id", match=MatchValue(value=verified_user_id)))
```

| Dimension | Score |
|---|---|
| Drift risk | Very low — one library, both services import |
| Deploy risk | Very low — additive filter; no API contract change |
| Defense-in-depth | High — server fully owns the narrowing; client cannot bypass |
| Refactor cost | Small — new shared lib (1 function), 2 import updates |

**Verdict**: preferred. The shared library captures the template once; the filter is server-side enforced regardless of what the client sends. Defence-in-depth is real: even if the hook is bypassed (direct curl, future MCP, debug call), the canonical narrowing applies.

### Option D: Chunk-metadata refactor (`kb_kind=personal_canonical`)

Stamp a new metadata field on every personal-KB chunk at ingest time. Retrieval-api filters on `kb_kind=personal_canonical AND user_id=U` instead of on slug strings.

| Dimension | Score |
|---|---|
| Drift risk | None — slug string irrelevant to filter |
| Deploy risk | High — requires re-stamping millions of existing chunks |
| Defense-in-depth | High |
| Refactor cost | Very large — ingest pipeline + qdrant payload index + migration |

**Verdict**: best long-term but not worth the migration cost for this fix. Defer to a future SPEC if the slug-template ever needs to change.

## 4. Decision

**Option C: Server-derived canonical slug via shared library.**

Rationale:
1. Defense-in-depth is the right level for "personal-KB leak". Client-side narrowing depends on every caller doing the right thing; server-side narrowing is the contract.
2. Shared library captures the slug template once. No drift between portal-api provisioning and retrieval-api search.
3. Backwards compatible — no API contract change, no new field on RetrieveRequest. knowledge-mcp/partner_chat unaffected (they don't use scope=personal).
4. The existing RBAC strip (REQ-17) can stay — it loses its over-broad effect because the canonical filter is added back server-side after the strip.

We will also fix the **scope=both** personal-portion leak in the same SPEC (Phase 2). Same shared library, same canonical filter, applied inside the visibility-should clause.

## 5. Implementation roadmap

### Phase 1: Shared library + scope=personal enforcement (critical)

1. Create `klai-libs/kb-slugs` package exporting `personal_kb_slug(user_id: str) -> str`.
2. portal-api: replace inline `personal_kb_slug` in `app.services.default_knowledge_bases` with the shared import.
3. retrieval-api: import the shared lib in `retrieval_api/services/search.py`.
4. `_scope_filter` for `scope=personal`: always append `kb_slug = personal_kb_slug(verified_user_id)` filter. Replaces the user_id-only condition or runs alongside.
5. Tests: update `test_role_filter.py` (the rewrite mirror), add `test_scope_filter.py` canonical-narrowing assertions, add regression test for the Jantine scenario.

### Phase 2: scope=both personal-portion enforcement (defense)

1. `_scope_filter` for `scope=both`: in the `visibility_should` clause, replace `(visibility=private AND user_id=U)` with `(visibility=private AND user_id=U AND kb_slug=canonical_slug)`.
2. Tests: new `test_scope_both_excludes_non_canonical_personal_chunks`.

### Phase 3: Strip-rule simplification (clean-up)

After Phase 1+2, the RBAC strip rule (lines 258-262 in retrieve.py) is no longer needed for canonical-narrowing purposes. It remains valuable for "personal-role can't query org_slug". Keep it; add a code comment cross-referencing this SPEC so the next reader understands why the strip is narrower in intent than its implementation.

### Phase 4: Hook clean-up (optional)

The LiteLLM-hook's PR #715 client-side narrowing becomes redundant. Two options:
- Keep it — defense-in-depth, two layers narrow to the same set.
- Remove it — single source of truth, fewer moving parts.

**Decision**: keep it. The cost is one extra field on the request; the benefit is a fast-fail path if retrieval-api ever loses the canonical filter (loud test failure instead of silent leak).

## 6. References

- **Code**: `klai-retrieval-api/retrieval_api/services/search.py:69-115`, `klai-retrieval-api/retrieval_api/api/retrieve.py:242-262`, `klai-portal/backend/app/services/default_knowledge_bases.py:22-24`, `deploy/litellm/klai_knowledge.py:1944-1993`
- **Related SPECs**: SPEC-PORTAL-RBAC-REFACTOR-001 REQ-17 (RBAC strip), SPEC-SEC-IDENTITY-ASSERT-001 (verify_body_identity), SPEC-PORTAL-KB-OWNERSHIP-001 (route-level firewall)
- **Recent PRs**: #705 (initial hook narrowing — Jantine fix), #715 (shared `no_citable_sources_message` lib), #716 (`personal_kb_slug` field on KnowledgeFeatureResponse)
- **Pitfall class**: `url-shape-multi-file-drift` (multi-file contract drift) and the "single gatekeeper" anti-pattern in CLAUDE.md
