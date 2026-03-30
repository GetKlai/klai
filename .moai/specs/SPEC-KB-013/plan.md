# SPEC-KB-013: Implementation Plan

| Field   | Value                                      |
|---------|--------------------------------------------|
| SPEC ID | SPEC-KB-013                                |
| Title   | KB Scope Control Bar (iframe-native)       |
| Created | 2026-03-27                                 |
| Updated | 2026-03-27                                 |

---

## 1. Module Decomposition

### M1 — Hook Enhancement (`deploy/litellm/klai_knowledge.py`)

**Scope:** Add version-based KB preference reading and pre-step skip to the LiteLLM hook.

**Files to modify:**
- `deploy/litellm/klai_knowledge.py`
- `deploy/litellm/tests/test_klai_knowledge_hook.py`

**Dependencies:** M2 must be deployed first (internal API extension), or the hook must handle the old response format gracefully.

**Key additions:**

1. `_get_kb_feature(user_id, org_id, cache)` — replaces `_check_user_feature`; reads the full KB preference response using a two-level version cache
2. Pre-step logic in `async_pre_call_hook`: if `kb_retrieval_enabled=False` → return early, no retrieval-api call
3. Pass `kb_slugs` to retrieval-api when `kb_slugs_filter` is set

**Implementation notes:**
- Two-level cache: `kb_ver:{org_id}:{user_id}` (30s TTL) as version pointer, `kb_feature:{org_id}:{user_id}:{version}` (300s TTL) for feature data
- Backward compatibility: if the portal returns the old `{"enabled": bool}` format, treat `kb_retrieval_enabled=True` and `kb_slugs_filter=None` as defaults
- Single portal call: the existing feature gate URL already returns the full feature response — no second HTTP roundtrip needed

**Reference:** `deploy/litellm/klai_knowledge.py:82` — existing `_check_user_feature` to replace.

---

### M2 — Portal Backend

**Scope:** DB migration, extended internal API response, new account preference endpoints.

**Files to create:**
- `klai-portal/backend/alembic/versions/{hash}_add_user_kb_preference.py`

**Files to modify:**
- `klai-portal/backend/app/models/portal.py` — add 4 columns to `PortalUser`
- `klai-portal/backend/app/api/internal.py` — extend `KnowledgeFeatureResponse`, read new columns
- `klai-portal/backend/app/api/app_account.py` — add GET + PATCH `/api/app/account/kb-preference`

**Dependencies:** None (can be implemented independently).

**Key decisions:**
- `kb_slugs_filter` stored as PostgreSQL `ARRAY(String(128))` — avoids JSONB complexity for a simple string list
- `kb_personal_enabled` is a boolean (default `true`) — controls `scope="both"` vs `scope="org"` in the hook; independent of `kb_slugs_filter`
- `kb_pref_version` is an integer counter — incremented on every PATCH, never reset
- The internal feature gate endpoint reads all four new fields — no extra query (already loading the user row)
- Slug validation in PATCH: query `portal_knowledge_bases` where `kb.slug IN (submitted_slugs) AND kb.org_id = user.org_id`; any unknown slug → 400 Bad Request
- Empty list `[]` in `kb_slugs_filter` normalized to `null` on save (prevents "empty list = all KBs" ambiguity)

**Access control:** The KB preference endpoint uses the existing `bearer` + `_get_caller_org` pattern. Only the authenticated user can read/write their own preference.

---

### M3 — Portal Frontend

**Scope:** `KBScopeBar` control bar rendered above the LibreChat iframe in the chat route.

**Files to create:**
- `klai-portal/frontend/src/routes/app/_components/KBScopeBar.tsx`

**Files to modify:**
- `klai-portal/frontend/src/routes/app/chat.tsx` — add `KBScopeBar` above the iframe
- `klai-portal/frontend/src/lib/logger.ts` — add `chatKbLogger`
- `klai-portal/frontend/messages/en.json` — add `chat_kb_*` keys
- `klai-portal/frontend/messages/nl.json` — add `chat_kb_*` keys (Dutch)

**Dependencies:** M2 (API must exist).

**Component structure:**

```
ChatPage (chat.tsx)
  +-- KBScopeBar (new, ~44px height)
  |    +-- Toggle: kb_retrieval_enabled (on/off)
  |    +-- (when ON, has KBs) multi-select / badge list
  |         +-- "Alle kennisbanken" state (when kb_slugs_filter is null)
  |         +-- Removable KB badges (when kb_slugs_filter is set)
  |         +-- Dropdown to add more KBs
  |    +-- (when OFF) muted label "Kennisbank uitgeschakeld"
  |    +-- (when no KBs in org) toggle only, no selector
  +-- <iframe> (flex-1, unchanged)
```

**Data fetching:**
- `useQuery(['kb-preference', token])` → `GET /api/app/account/kb-preference`
- `useQuery(['app-kbs', token])` → `GET /api/app/knowledge-bases` (selector options)
- `useMutation` for save → invalidates `kb-preference` query on success
- Auto-save on change: no explicit save button

**UI patterns:**
- Slim bar: `border-b border-[var(--color-border)]`, ~44px height
- Follows existing portal component conventions (`Button`, `Select` from `components/ui/`)
- Error text uses `--color-destructive` token
- Loading state: skeleton or disabled state during mutation

**i18n keys (`chat_kb_*`):**

```
chat_kb_bar_label
chat_kb_toggle_on
chat_kb_toggle_off
chat_kb_selector_all
chat_kb_selector_placeholder
chat_kb_badge_remove
chat_kb_error_save
chat_kb_error_fetch
chat_kb_no_kbs
chat_kb_saving
```

---

### M4 — LibreChat Config (Deferred)

**Status:** Deferred pending research into LibreChat dynamic configuration (see `research-librechat-dynamic-config.md`).

Without tag parsing in the hook (removed in v0.2.0), a pre-configured model spec with `[klai-kb:off]` in its system prompt has no effect. M4 is only viable once either:
- Tag parsing is re-introduced as an optional mechanism (out of scope for v0.2.0), or
- LibreChat dynamic configuration research yields a viable injection point

---

## 2. Implementation Order

### Step 1: Backend (M2) — deploy first

**Deliverables:**
- 3 new columns on `portal_users` with Alembic migration
- Extended internal feature gate response including `kb_pref_version`
- GET + PATCH `/api/app/account/kb-preference` endpoints

**Why first:** The hook has a graceful fallback for the old API format, so M1 can be deployed before or after M2. But M2 must exist before M3 (frontend) is usable.

**Verification:** Set `kb_retrieval_enabled=false` directly in DB → verify no retrieval-api calls are made via hook logs.

---

### Step 2: Hook (M1) — can deploy before or after M2

**Deliverables:**
- `_get_kb_feature` with version-based two-level cache
- Pre-step skip when `kb_retrieval_enabled=false`
- `kb_slugs` passed to retrieval-api when `kb_slugs_filter` is set

**Why parallel with M2:** Backward compatible — old API response (`{"enabled": bool}`) treated as `kb_retrieval_enabled=True, kb_slugs_filter=None`.

**Verification:** Unit tests cover all hook behaviors. Integration: set preference in DB, verify retrieval-api is or isn't called.

---

### Step 3: Frontend (M3) — after M2

**Deliverables:**
- `KBScopeBar` component in chat route
- Toggle + multi-select KB filter
- Auto-save via PATCH endpoint
- Full EN + NL i18n

**Verification:** Toggle KB off in control bar → send chat message → confirm no KB context injected. Select specific KB → confirm only that KB's chunks appear.

---

## 3. Technical Approach

### 3.1 Version-Based Cache (Hook)

```python
async def _get_kb_feature(user_id: str, org_id: str, cache) -> dict:
    """Returns {"enabled": bool, "kb_retrieval_enabled": bool, "kb_slugs_filter": list|None, "version": int}"""
    # Step 1: check version pointer (short-lived)
    version_key = f"kb_ver:{org_id}:{user_id}"
    cached_version = await cache.async_get_cache(version_key)

    if cached_version is not None:
        feature_key = f"kb_feature:{org_id}:{user_id}:{cached_version}"
        cached = await cache.async_get_cache(feature_key)
        if cached is not None:
            return cached  # cache hit: no portal call needed

    # Cache miss — fetch fresh from portal
    resp = await client.get(
        f"{PORTAL_API_URL}/internal/v1/users/{user_id}/feature/knowledge",
        params={"org_id": org_id},
        headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
    )
    data = resp.json()
    version = data.get("kb_pref_version", 0)

    result = {
        "enabled": data.get("enabled", False),
        "kb_retrieval_enabled": data.get("kb_retrieval_enabled", True),
        "kb_personal_enabled": data.get("kb_personal_enabled", True),
        "kb_slugs_filter": data.get("kb_slugs_filter"),
        "version": version,
    }

    # Store: version pointer expires in 30s; feature data expires in 300s
    await cache.async_set_cache(version_key, str(version), ttl=30)
    await cache.async_set_cache(f"kb_feature:{org_id}:{user_id}:{version}", result, ttl=300)
    return result
```

**Pre-step in `async_pre_call_hook`:**

```python
feature = await _get_kb_feature(user_id, org_id, cache)
if not feature["enabled"]:
    return data  # existing: no entitlement
if not feature["kb_retrieval_enabled"]:
    return data  # NEW: user disabled KB retrieval

scope = "both" if feature.get("kb_personal_enabled", True) else "org"
kb_slugs = feature.get("kb_slugs_filter")  # None = all org KBs within scope
# ... call retrieval-api with scope and kb_slugs ...
```

### 3.2 PostgreSQL ARRAY columns

```python
from sqlalchemy import ARRAY, String

class PortalUser(Base):
    # ...
    kb_retrieval_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    kb_personal_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    kb_slugs_filter: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(128)), nullable=True
    )
    kb_pref_version: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
```

Alembic migration:
```python
op.add_column('portal_users',
    sa.Column('kb_retrieval_enabled', sa.Boolean(), nullable=False, server_default='true'))
op.add_column('portal_users',
    sa.Column('kb_personal_enabled', sa.Boolean(), nullable=False, server_default='true'))
op.add_column('portal_users',
    sa.Column('kb_slugs_filter', postgresql.ARRAY(sa.String(128)), nullable=True))
op.add_column('portal_users',
    sa.Column('kb_pref_version', sa.Integer(), nullable=False, server_default='0'))
```

### 3.3 Cache Invalidation Timing

When the user saves a new preference via the control bar:
1. PATCH endpoint increments `kb_pref_version` (e.g. 4 → 5) and saves to DB
2. Hook still has `kb_ver:{org_id}:{user_id}` = "4" cached (30s TTL)
3. Within 30 seconds: `version_key` expires → hook fetches fresh → gets version 5 → caches under new key
4. Old `kb_feature:...:4` entry is never accessed again and expires naturally after 300s

**Effective latency: up to 30 seconds.** This is acceptable for a user-driven preference toggle.

---

## 4. Risks and Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Hook deployed before M2 (extended API) | Medium | Low | Hook falls back gracefully — old `{"enabled": bool}` treated as `kb_retrieval_enabled=True, kb_slugs_filter=None` |
| R2 | Frontend shows stale KB list after a KB is deleted | Low | Low | KBs re-fetched on control bar mount; stale slugs in `kb_slugs_filter` produce 0 retrieval results (safe) |
| R3 | ARRAY column requires `asyncpg` type support | Low | Low | `asyncpg` supports PostgreSQL ARRAY natively; already used in this project |
| R4 | 30s cache lag feels too slow for users | Low | Low | 30s TTL is acceptable; if faster is needed, portal PATCH can additionally call `cache.async_delete_cache(version_key)` via LiteLLM admin API — out of scope for v1 |
| R5 | Control bar adds visual noise when user has no KBs | Low | Low | REQ-S3: hide the selector entirely when org has 0 KBs; show toggle only |

---

## 5. File Impact Summary

### New Files (2)

| File | Module |
|------|--------|
| `klai-portal/backend/alembic/versions/{hash}_add_user_kb_preference.py` | M2 |
| `klai-portal/frontend/src/routes/app/_components/KBScopeBar.tsx` | M3 |

### Modified Files (8)

| File | Module | Change |
|------|--------|--------|
| `deploy/litellm/klai_knowledge.py` | M1 | Version-based cache, pre-step skip, `kb_slugs` passthrough |
| `deploy/litellm/tests/test_klai_knowledge_hook.py` | M1 | New tests for all hook behaviors |
| `klai-portal/backend/app/models/portal.py` | M2 | Add 4 columns to `PortalUser` |
| `klai-portal/backend/app/api/internal.py` | M2 | Extend `KnowledgeFeatureResponse` with KB preference fields |
| `klai-portal/backend/app/api/app_account.py` | M2 | Add GET + PATCH `/api/app/account/kb-preference` |
| `klai-portal/frontend/src/routes/app/chat.tsx` | M3 | Add `<KBScopeBar />` above iframe, flex-col layout |
| `klai-portal/frontend/src/lib/logger.ts` | M3 | Add `chatKbLogger` |
| `klai-portal/frontend/messages/en.json` + `nl.json` | M3 | Add `chat_kb_*` keys |

---

## 6. Next Steps

After SPEC approval:
1. Run `/moai run SPEC-KB-013` to begin implementation
2. Start M2 (backend) — no dependencies, can begin immediately
3. Start M1 (hook) in parallel — graceful fallback means it's safe regardless of M2 deploy order
4. Follow with M3 (frontend) once M2 API is live
5. M4 (LibreChat config) deferred — revisit after dynamic config research concludes
