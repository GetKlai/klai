# SPEC-KB-013: Acceptance Criteria

| Field   | Value                                      |
|---------|--------------------------------------------|
| SPEC ID | SPEC-KB-013                                |
| Title   | KB Scope Control Bar (iframe-native)       |
| Created | 2026-03-27                                 |
| Updated | 2026-03-27                                 |

---

## Module 1 — Hook

### AC-1.1 — Pre-step skip when KB retrieval disabled

**Given** a user with knowledge entitlement and `kb_retrieval_enabled=false` in their portal preference
**When** a chat message is sent
**Then** the hook makes no HTTP call to retrieval-api
**And** the chat response is not augmented with KB context

### AC-1.2 — Default behavior preserved when no preference set

**Given** a user with knowledge entitlement and no KB preference set
**When** the hook processes a non-trivial message
**Then** the hook calls retrieval-api with `scope="both"` and `kb_slugs=None` (all KBs, same as current behavior)

### AC-1.3 — `kb_slugs_filter` passed to retrieval-api when set

**Given** a user with `kb_slugs_filter=["hr-docs"]` in their portal preference
**When** the hook calls retrieval-api
**Then** the request body contains `"kb_slugs": ["hr-docs"]`

### AC-1.7 — `kb_personal_enabled=true` maps to `scope="both"`

**Given** a user with `kb_personal_enabled=true` (default)
**When** the hook calls retrieval-api
**Then** the request body contains `"scope": "both"`

### AC-1.8 — `kb_personal_enabled=false` maps to `scope="org"`

**Given** a user with `kb_personal_enabled=false`
**When** the hook calls retrieval-api
**Then** the request body contains `"scope": "org"`
**And** personal KB chunks are not returned

### AC-1.4 — Cache invalidation: fresh preference within 30s after change

**Given** a user changes their KB preference in the control bar (portal increments `kb_pref_version`)
**When** the `kb_ver` cache key expires (within 30 seconds) and a new chat message is sent
**Then** the hook fetches a fresh preference from the portal
**And** the new preference takes effect for subsequent messages

### AC-1.5 — Cache hit: no portal call when version is current

**Given** the hook has a cached feature entry for the current `kb_pref_version`
**When** a chat message is sent
**Then** the hook does NOT call the portal internal API
**And** the cached feature data is used directly

### AC-1.6 — Portal API unreachable: graceful fallback

**Given** the portal internal API is unreachable
**When** the hook attempts to fetch KB preference
**Then** the hook falls back to all-KBs behavior (`scope="both"`, no `kb_slugs` filter)
**And** the chat is not blocked

---

## Module 2 — Portal Backend

### AC-2.1 — Org-scoped KB slugs only

**Given** a user belonging to org A
**When** they PATCH `/api/app/account/kb-preference` with slugs from org B
**Then** the API returns `400 Bad Request`

### AC-2.2 — KB preference saved and retrievable

**Given** a user with no KB preference set
**When** they PATCH `/api/app/account/kb-preference` with `{"kb_retrieval_enabled": false, "kb_slugs_filter": null}`
**And** then GET `/api/app/account/kb-preference`
**Then** the response contains `{"kb_retrieval_enabled": false, "kb_slugs_filter": null}`

### AC-2.3 — Internal API returns full KB preference including version and personal flag

**Given** a user with `kb_retrieval_enabled=false`, `kb_personal_enabled=true`, and `kb_pref_version=3` in the DB
**When** the LiteLLM hook calls `GET /internal/v1/users/{user_id}/feature/knowledge`
**Then** the response contains `{"enabled": true, "kb_retrieval_enabled": false, "kb_personal_enabled": true, "kb_slugs_filter": null, "kb_pref_version": 3}`

### AC-2.4 — Empty slug list normalized to null

**Given** a PATCH request with `"kb_slugs_filter": []`
**When** the portal saves the preference
**Then** `kb_slugs_filter` is stored as `null` (not an empty array)

### AC-2.5 — Unauthorized slug rejected

**Given** a user who does not have access to KB slug "secret-kb"
**When** they PATCH `/api/app/account/kb-preference` with `{"kb_slugs_filter": ["secret-kb"]}`
**Then** the API returns `400 Bad Request`

### AC-2.6 — `kb_pref_version` incremented on every save

**Given** a user with `kb_pref_version=2`
**When** they PATCH `/api/app/account/kb-preference` with any valid body
**Then** `kb_pref_version` in the DB is `3`
**And** subsequent GET returns `kb_pref_version: 3`

---

## Module 3 — Frontend (KBScopeBar)

### AC-3.1 — Control bar reflects persisted preference on chat page load

**Given** a user with `kb_retrieval_enabled=false` saved in the portal
**When** they navigate to the chat page
**Then** the control bar shows "Kennisbank uitgeschakeld" state (not the default "on" state)

### AC-3.2 — Deleted KB slugs not shown in control bar selector

**Given** a user has `kb_slugs_filter=["old-kb"]` but "old-kb" no longer exists in the org
**When** the user opens the chat page
**Then** "old-kb" is not shown as a selectable or selected option in the control bar
**And** the multi-select shows only currently existing KBs

### AC-3.3 — Visual indicator when KB retrieval is off

**Given** the user toggles KB retrieval off in the control bar
**When** the toggle is in the off state
**Then** the bar shows a muted label "Kennisbank uitgeschakeld" and the KB selector is hidden

### AC-3.4 — Loading state during save mutation

**Given** the user changes the KB scope in the control bar
**When** the mutation is pending (PATCH in flight)
**Then** the control bar shows a loading/saving indicator
**And** the toggle and selector are disabled to prevent duplicate submissions

### AC-3.5 — No KB selector shown when org has no KBs

**Given** the user's org has zero knowledge bases
**When** the user opens the chat page
**Then** the control bar shows only the on/off toggle and no selector

### AC-3.6 — "Alle org-kennisbanken" shown when no org filter is active

**Given** a user with `kb_slugs_filter=null` and KB retrieval enabled
**When** they open the chat page
**Then** the org KB dropdown shows "Alle org-kennisbanken" (not an empty selection state)

### AC-3.7 — KBScopeBar hidden when user has no KB entitlement

**Given** a user with no knowledge entitlement (`feature.enabled=false`)
**When** they navigate to the chat page
**Then** the KBScopeBar does not render (no bar above the iframe)
**And** the iframe takes the full height of the chat container

### AC-3.8 — Control bar collapsible

**Given** the user is on the chat page with KB entitlement
**When** they click the collapse toggle
**Then** the full control bar collapses to a compact inline label showing the current scope (e.g. "Kennisbank: Alles", "Kennisbank: uit", "Kennisbank: Engineering")
**When** they click again
**Then** the full controls expand

### AC-3.9 — "Wis filter" resets org KB filter to null

**Given** a user has one or more org KBs selected in the dropdown
**When** they click "Wis filter"
**Then** `kb_slugs_filter` is set to `null` and saved
**And** the dropdown returns to "Alle org-kennisbanken"

---

## Edge Cases

### EC-1 — Feature gate disabled overrides everything

**Given** a user with no knowledge entitlement (`enabled=false` in feature gate)
**When** the hook checks the feature gate
**Then** retrieval is skipped entirely (existing behavior)
**And** `kb_retrieval_enabled` and `kb_slugs_filter` are not consulted (entitlement check runs first)

### EC-2 — Personal KB excluded when filter is active

**Given** a user with `kb_slugs_filter=["engineering"]`
**When** the hook calls retrieval-api with `scope="both"` and `kb_slugs=["engineering"]`
**Then** personal KB chunks (kb_slug="personal") are excluded because "personal" is not in the filter

*Note: This is correct behavior. The filter applies to all scopes including personal. If the user wants personal KB included alongside org KBs, they must add "personal" to their `kb_slugs_filter`.*
