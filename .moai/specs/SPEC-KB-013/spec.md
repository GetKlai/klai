---
id: SPEC-KB-013
version: 0.2.0
status: completed
created: 2026-03-27
updated: 2026-03-27
author: mark
priority: high
---

# SPEC-KB-013: KB Scope Control Bar (iframe-native)

| Field      | Value                                             |
|------------|---------------------------------------------------|
| SPEC ID    | SPEC-KB-013                                       |
| Title      | KB Scope Control Bar (iframe-native)              |
| Created    | 2026-03-27                                        |
| Status     | Completed                                         |
| Priority   | High                                              |
| Domain     | Knowledge Base / Chat / Frontend                  |
| Depends On | SPEC-KB-008 (retrieval-api), SPEC-KB-010 (hook)   |

## HISTORY

| Version | Date       | Change                                                    |
|---------|------------|-----------------------------------------------------------|
| 0.2.0   | 2026-03-27 | Replaced system-prompt-tag approach with iframe control bar + version-based cache invalidation |
| 0.1.0   | 2026-03-27 | Initial draft (system prompt tag convention)              |

---

## 1. Environment

### 1.1 System Context

LibreChat is embedded as a full-screen iframe inside the Klai portal (`portal/frontend/src/routes/app/chat.tsx`). The portal controls the surrounding layout. The iframe has no awareness of its container.

The `KlaiKnowledgeHook` in LiteLLM intercepts every chat message and injects KB context. It currently searches all org KBs on every message. Users cannot:

- Disable KB retrieval for the current conversation
- Restrict retrieval to a specific subset of KBs

### 1.2 Chosen Approach

Because the portal owns the iframe container, a **control bar rendered above the iframe** is fully within the portal's React codebase. No LibreChat modification, no fork, no system prompt conventions.

The control bar writes a preference to the portal DB. The LiteLLM hook reads it on the next chat message via the existing internal portal API. **Version-based cache invalidation** (a counter on `PortalUser`) ensures the hook picks up changes within 30 seconds — the version pointer cache TTL.

```
┌─────────────────────────────────────────────────────────────────────┐
│  🔍 Kennisbank:  [Alles ▾]  of  [Engineering ×] [Product-docs ×]   │
└─────────────────────────────────────────────────────────────────────┘
│                                                                       │
│                        LibreChat iframe                               │
│                        (onveranderd)                                  │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

### 1.3 The Pre-Step Requirement

The hook currently always fires a retrieval-api HTTP call for eligible users. This SPEC adds an explicit **pre-retrieval gate**: when the effective KB scope is "disabled", the hook returns `data` unchanged without calling retrieval-api at all.

---

## 2. Assumptions

| # | Assumption | Confidence | Risk if Wrong |
|---|-----------|------------|---------------|
| A1 | `chat.tsx` is the only portal route that renders the LibreChat iframe | High | Additional routes need the same control bar |
| A2 | Version-based cache invalidation (incrementing `kb_pref_version`) is sufficient for instant effect; no LiteLLM cache API calls needed | High | Would need a separate cache-bust endpoint |
| A3 | `GET /api/app/knowledge-bases` returns all KBs accessible to the authenticated user, sufficient for populating the control bar selector | High | Need a scoped KB listing endpoint |
| A4 | The control bar height is small enough (~44px) that the chat iframe does not feel cramped | High | May need collapse/expand behavior |
| A5 | Cross-origin restrictions between the portal (`{tenant}.getklai.com`) and LibreChat (`chat-{tenant}.getklai.com`) prevent postMessage — the portal cannot inject values directly into LibreChat | High | If same-origin were possible, postMessage would be an alternative |

---

## 3. Requirements

### 3.1 Ubiquitous Requirements (shall — always active)

**REQ-U1:** The system shall never inject KB context for a user whose effective KB scope is "disabled".

**REQ-U2:** The system shall preserve existing behavior (all KBs, retrieval enabled) when no preference has been set by the user.

**REQ-U3:** The system shall enforce org-scoped isolation: `kb_slugs_filter` may only contain slugs belonging to the user's own org.

### 3.2 Event-Driven Requirements (when...shall)

**REQ-E1:** When the user changes the KB scope in the control bar, the portal shall persist the new preference immediately and increment `kb_pref_version`.

**REQ-E2:** When the LiteLLM hook calls the portal's internal feature gate endpoint, the response shall include `kb_retrieval_enabled`, `kb_slugs_filter`, and `kb_pref_version`.

**REQ-E3:** When the hook's version pointer cache key (`kb_ver`) expires (30s TTL), the hook shall fetch a fresh preference from the portal, cache it under the new version key, and use the updated preference for the current and subsequent requests.

**REQ-E4:** When `kb_retrieval_enabled=false` is the effective preference, the hook shall return `data` unchanged without calling retrieval-api (pre-step skip).

**REQ-E5:** When `kb_slugs_filter` is a non-empty list, the hook shall pass it as `kb_slugs` to retrieval-api, restricting results to those KBs only.

**REQ-E6:** When the user selects "Alles" (all KBs) in the control bar, `kb_slugs_filter` shall be set to `null` (not an empty list).

**REQ-E7:** When the control bar loads, it shall fetch and reflect the user's currently persisted KB preference.

**REQ-E8:** When `kb_personal_enabled=true`, the hook shall call retrieval-api with `scope="both"` (personal + org). When `kb_personal_enabled=false`, the hook shall call retrieval-api with `scope="org"` (org only, no personal KB chunks).

### 3.3 State-Driven Requirements (while...shall)

**REQ-S1:** While `kb_retrieval_enabled=false`, the control bar shall display a clear visual indicator that KB retrieval is off.

**REQ-S2:** While the KB preference is being saved (mutation pending), the control bar shall show a loading state and prevent duplicate submissions.

**REQ-S3:** While there are no KBs in the org, the control bar shall display only the on/off toggle and hide the KB selector.

**REQ-S4:** While the user has no knowledge entitlement (`feature.enabled=false`), the `KBScopeBar` shall not render at all.

### 3.4 Optional Requirements (where...shall)

**REQ-O1:** Where the control bar shows specific KB slugs selected, each selected KB shall be shown as a removable badge (×) for quick deselection.

**REQ-O2:** The control bar shall be collapsible. In the collapsed state it shows a compact indicator of the current KB scope (e.g. "Kennisbank: Alles" or "Kennisbank: uit"). In the expanded state the full controls are visible.

**REQ-O3:** Where the org KB selector has one or more KBs selected, a "Wis filter" button shall reset the org filter to null (all org KBs).

### 3.5 Unwanted Behavior Requirements (if...then...shall)

**REQ-N1:** If the portal API is unreachable when the hook checks KB preference, the hook shall fall back to all-KBs behavior (fail-open on retrieval, not fail-closed).

**REQ-N2:** If `kb_slugs_filter` contains slugs that no longer exist, the retrieval-api shall return 0 results for those slugs without error; the control bar shall not display deleted KB slugs.

**REQ-N3:** If a user attempts to save a `kb_slugs_filter` containing slugs they do not have access to, the portal API shall return `400 Bad Request`.

---

## 4. Specifications

### 4.1 Control Bar Component (`chat.tsx` → `KBScopeBar`)

**Layout update to `chat.tsx`:**

```tsx
function ChatPage() {
  return (
    <div className="h-full w-full flex flex-col">
      <KBScopeBar />
      <iframe
        src={chatUrl}
        className="flex-1 w-full border-none"
        title="Chat"
        allow="clipboard-write; microphone; screen-wake-lock"
      />
    </div>
  )
}
```

**`KBScopeBar` component** (new file: `routes/app/_components/KBScopeBar.tsx`):

- Only renders when `feature.enabled=true` (user has KB entitlement); otherwise returns `null`
- Collapsible: collapsed state shows a compact inline label ("Kennisbank: Alles" / "Kennisbank: uit" / "Kennisbank: 2 geselecteerd"); click expands
- Expanded bar (~44px height), `border-b border-[var(--color-border)]`

**Expanded layout (left → right):**

| Element | Condition | Behaviour |
|---------|-----------|-----------|
| Toggle "Kennisbank" | Always | Enables/disables KB retrieval entirely |
| Checkbox "Persoonlijke kennisbank" | When toggle ON | Controls `kb_personal_enabled`; determines whether personal KB chunks are included (maps to `scope="both"` vs `scope="org"` in the hook) |
| Org KB dropdown | When toggle ON | Multi-select checkboxes per org KB; default "Alle org-kennisbanken" (null filter) |
| "Wis filter" button | When org filter active | Resets `kb_slugs_filter` to null |
| Muted label "Kennisbank uitgeschakeld" | When toggle OFF | Replaces all selectors |

- Saves on each change (auto-save, no explicit save button)
- Uses `useMutation` with `PATCH /api/app/account/kb-preference`

**Data fetching:**
- `useQuery(['kb-preference', token])` → `GET /api/app/account/kb-preference`
- `useQuery(['app-kbs', token])` → `GET /api/app/knowledge-bases` (for the selector options)
- `useMutation` for save → invalidates `kb-preference` query on success

### 4.2 Portal Backend

#### `PortalUser` model additions

```python
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

`kb_personal_enabled` controls whether the hook uses `scope="both"` (personal + org) or `scope="org"`. It is independent of `kb_slugs_filter`, which filters only within org KBs.

#### Alembic migration

Four new columns on `portal_users`, all with defaults (no data migration needed).

#### Extended internal feature gate response

```python
class KnowledgeFeatureResponse(BaseModel):
    enabled: bool
    kb_retrieval_enabled: bool = True
    kb_personal_enabled: bool = True
    kb_slugs_filter: list[str] | None = None
    kb_pref_version: int = 0  # used as cache key discriminator
```

#### New portal API endpoints

```
GET  /api/app/account/kb-preference
PATCH /api/app/account/kb-preference
```

Request body for PATCH:
```json
{
  "kb_retrieval_enabled": true,
  "kb_personal_enabled": true,
  "kb_slugs_filter": ["engineering", "product-docs"]
}
```

On save: validate slugs against `portal_knowledge_bases` for the user's org, then increment `kb_pref_version`.

### 4.3 Hook: Version-Based Cache Invalidation

**Current cache key:** `"kb_authz:{org_id}:{user_id}"`

**New approach:** make a single portal call that returns both `enabled` and KB preference. Cache under a key that includes the version:

```python
async def _get_kb_feature(user_id: str, org_id: str, cache) -> dict:
    # Step 1: cheap check — is there a cached version number?
    version_key = f"kb_ver:{org_id}:{user_id}"
    cached_version = await cache.async_get_cache(version_key)

    if cached_version is not None:
        feature_key = f"kb_feature:{org_id}:{user_id}:{cached_version}"
        cached = await cache.async_get_cache(feature_key)
        if cached is not None:
            return cached

    # Cache miss or version mismatch — fetch fresh from portal
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
        "kb_slugs_filter": data.get("kb_slugs_filter"),
        "version": version,
    }

    # Cache: version pointer (short TTL 30s) + feature data (long TTL 300s)
    await cache.async_set_cache(version_key, str(version), ttl=30)
    await cache.async_set_cache(f"kb_feature:{org_id}:{user_id}:{version}", result, ttl=300)
    return result
```

When the user saves a new preference in the control bar, `kb_pref_version` increments. On the next hook call (within 30s), `version_key` expires or mismatches → fresh fetch → new version cached → new preference takes effect.

**Pre-step in `async_pre_call_hook`:**

```python
feature = await _get_kb_feature(user_id, org_id, cache)
if not feature["enabled"]:
    return data  # existing: no entitlement
if not feature["kb_retrieval_enabled"]:
    return data  # NEW: user disabled KB retrieval

# Determine scope from personal preference
scope = "both" if feature.get("kb_personal_enabled", True) else "org"

kb_slugs = feature.get("kb_slugs_filter")  # None = all org KBs within scope
# ... call retrieval-api with scope and kb_slugs ...
```

### 4.4 i18n Keys (`account_kb_*` → `chat_kb_*`)

Key prefix changed to `chat_kb_` since the component lives in the chat route, not account settings.

Estimated 14 keys:
```
chat_kb_bar_label
chat_kb_toggle_on
chat_kb_toggle_off
chat_kb_personal_label
chat_kb_org_selector_all
chat_kb_org_selector_placeholder
chat_kb_org_clear
chat_kb_badge_remove
chat_kb_error_save
chat_kb_error_fetch
chat_kb_no_kbs
chat_kb_saving
chat_kb_collapsed_all
chat_kb_collapsed_off
```

---

## 5. Traceability

| Requirement | Module | Test |
|-------------|--------|------|
| REQ-U1 | M1-hook | AC-1.1 |
| REQ-U2 | M1-hook | AC-1.2 |
| REQ-U3 | M2-backend | AC-2.1 |
| REQ-E1 | M2-backend, M3-frontend | AC-2.2, AC-2.6 |
| REQ-E2 | M2-backend | AC-2.3 |
| REQ-E3 | M1-hook | AC-1.4 |
| REQ-E4 | M1-hook | AC-1.1 |
| REQ-E5 | M1-hook | AC-1.3 |
| REQ-E6 | M2-backend | AC-2.4 |
| REQ-E7 | M3-frontend | AC-3.1 |
| REQ-E8 | M1-hook | AC-1.7, AC-1.8 |
| REQ-S1 | M3-frontend | AC-3.3 |
| REQ-S2 | M3-frontend | AC-3.4 |
| REQ-S3 | M3-frontend | AC-3.5 |
| REQ-S4 | M3-frontend | AC-3.7 |
| REQ-O1 | M3-frontend | AC-3.6 |
| REQ-O2 | M3-frontend | AC-3.8 |
| REQ-O3 | M3-frontend | AC-3.9 |
| REQ-N1 | M1-hook | AC-1.6 |
| REQ-N2 | M3-frontend | AC-3.2 |
| REQ-N3 | M2-backend | AC-2.5 |

---

## Implementation Notes

Implemented in commit `8c15dd8` (2026-03-27). All core requirements (M1–M4) delivered.

**Deferred items (removed from scope):**

- **AC-3.8** (collapsible control bar, REQ-O2): Removed from scope. The bar is always visible above the iframe; no collapse/expand toggle was implemented.
- **AC-3.9** ("Wis filter" reset button, REQ-O3): Removed from scope. Users can deselect org KBs by clicking individual checkboxes; no bulk-clear button was added.

**Partial implementation:**

- **AC-3.4** (input disable during pending mutation, REQ-S2): The bar shows "Saving…" text while the mutation is in flight, but the toggle and checkbox inputs are not disabled. Full disable was not implemented.

**Additional notes:**

- M0 (retrieval-api bugfix): A bugfix for `kb_slugs` filtering (org-only when `scope=both`) was included in this commit as a prerequisite, covered by 2 new tests in `retrieval-api/tests/test_scope_filter.py`.
- The two-level version cache (`kb_ver:{org_id}:{user_id}` with 30s TTL pointing to `kb_feature:{org_id}:{user_id}:{version}` with 300s TTL) ensures preference changes propagate to the LiteLLM hook within 30 seconds without requiring a direct cache-bust API call.
