# SPEC-CRAWL-004: Webcrawler Connector — Automatic Auth Guard Setup

**Status:** Deployed
**Priority:** Medium
**Created:** 2026-04-17
**Revised:** 2026-04-17 (AI-first approach, no manual config)
**Depends on:** SPEC-CRAWL-003 (deployed 2026-04-16)

---

## Problem

SPEC-CRAWL-003 added three content-quality detection layers, but Layers A (canary check)
and B (login indicator) require config fields (`canary_url`, `canary_fingerprint`,
`login_indicator_selector`) that can only be set via direct SQL. No admin will ever do that.

The fields themselves are technical artifacts that an admin shouldn't need to understand:
"canary fingerprint" is meaningless to someone who just wants their wiki connected. The
existing pattern in the wizard — "Let AI find the content selector" — shows a better model:
the system does the smart work, the admin just clicks a button.

---

## Goal

When an admin sets up a webcrawler connector with authentication cookies, the system
automatically activates auth guard protection. No extra fields, no extra steps.
The admin sees a confirmation that auth protection is enabled — not the technical details.

For advanced users: a way to inspect and override the auto-detected settings.

---

## Non-Goals

- **Quality status UI.** Showing `quality_status` on connector list or sync history is a
  separate observability SPEC.
- **Auto-refresh of expired cookies.** Out of scope per SPEC-CRAWL-003.
- **Canary for non-authenticated connectors.** Public sites don't need auth guard — Layer C
  already covers them automatically.

---

## Design Principle

Follow the existing "Let AI find the content selector" pattern:
1. The system does the work automatically during an existing step
2. The admin sees the result as a simple confirmation
3. Advanced users can override via a toggle/link

The auth guard setup hooks into the **Preview step** (step 3 of the wizard), which already
has everything needed: a real page URL, cookies, and the crawl result.

---

## Environment

- **Frontend:** React 19, TanStack Router, Paraglide i18n, shadcn/ui components
- **Backend:** FastAPI, Pydantic v2, SQLAlchemy 2.0 async
- **Existing wizard:** `$kbSlug_.add-connector.tsx` — step 3 (Preview) already runs a crawl
  with cookies via `POST /ingest/v1/crawl/preview`. The `try_ai` flag triggers AI selector
  detection. Same flow can trigger auth guard detection.
- **AI selector detection:** `knowledge-ingest/routes/crawl.py` — `detect_selector_via_llm()`
  analyzes DOM summary and proposes a CSS selector. Same pattern reusable for login indicator.
- **klai-connector:** `_post_crawl_sync()` shared crawl helper; `compute_content_fingerprint()`
  for SimHash. Both available for the canary fingerprint computation.
- **Cookie storage:** Cookies are stored in the connector's `config` JSONB field as
  `cookies: list[CookieEntry]`. For connectors with encrypted credentials, the portal
  decrypts via `connector_credentials.py` before passing to klai-connector.

---

## Requirements

### Auto-Detection During Preview (AI-First)

**REQ-1: Auth guard activates automatically when cookies are present**
WHEN a preview crawl succeeds (word_count >= 100) AND the connector has cookies configured,
the preview response SHALL include auth guard suggestions:
```json
{
  "auth_guard": {
    "canary_url": "https://wiki.example.com/previewed-page",
    "canary_fingerprint": "abc123def4567890",
    "login_indicator_selector": ".user-menu-dropdown",
    "login_indicator_description": "User menu in the top-right corner"
  }
}
```
The preview URL becomes the canary page. The fingerprint is computed from the crawled
content. The login indicator is detected by AI from the page's DOM.

**REQ-2: Login indicator detection via AI**
WHEN `auth_guard` is being computed AND the page's DOM is available, the system SHALL
use the existing `crawl_dom_summary()` + LLM pattern (from SPEC-CRAWL-001) to detect
which DOM element indicates an authenticated session. The LLM prompt SHALL ask:

> "Given this DOM structure of a page behind authentication, which CSS selector
> identifies an element that is ONLY visible to logged-in users? Look for:
> logout buttons, user avatars, account menus, dashboard links. Return a single
> CSS selector."

If no indicator is found or the LLM returns low-confidence, `login_indicator_selector`
is set to `null` and Layer B stays disabled for this connector. This is non-blocking.

**REQ-3: Canary fingerprint computed from preview content**
The fingerprint SHALL be computed by calling `compute_content_fingerprint()` on the
preview crawl's `fit_markdown`. No extra crawl request needed — the preview already
has the content. If the content has < 20 words (returns empty fingerprint), the canary
is skipped and the preview response's `auth_guard.canary_url` is `null`.

**REQ-4: Frontend shows confirmation, not technical details**
After a successful preview with cookies, the UI SHALL show a status line below the
preview content:

```
✓ Auth protection enabled
  We'll check "Getting Started" before every sync to detect expired logins.
  Pages without login indicator will be excluded.
  
  ⚙ Advanced settings  ←  expandable link
```

The "Advanced settings" link expands to show:
- Canary page URL (editable text field, pre-filled with detected value)
- Login indicator selector (editable text field, pre-filled with detected value)

Both fields are pre-filled by the auto-detection. The admin can leave them as-is (most
common) or modify them for edge cases.

If no cookies are configured, the auth guard section is hidden entirely.

**REQ-5: Auto-detected values saved with connector config**
WHEN the connector is saved (step 4 → submit), the frontend SHALL include
`canary_url`, `canary_fingerprint`, and `login_indicator_selector` in the config JSON
if they were detected during preview. The backend's existing XOR validator
(SPEC-CRAWL-003) ensures consistency.

### Backend — Preview Response Extension

**REQ-6: Extend crawl-preview response with auth_guard field**
The `POST /ingest/v1/crawl/preview` response (`CrawlPreviewResponse`) SHALL add an
optional `auth_guard` field. This field is only populated when:
- `cookies` is non-empty in the request
- `word_count >= 100` (preview succeeded with real content)
- `compute_content_fingerprint()` returns a non-empty fingerprint

Implementation: add the fingerprint computation to the existing `preview_crawl()` handler
in `knowledge-ingest/routes/crawl.py` (already imports crawl4ai; compute fingerprint from
`fit_md`). For login indicator: call `crawl_dom_summary()` + new `detect_login_indicator_via_llm()`
(mirrors `detect_selector_via_llm()` with a different prompt).

**REQ-7: compute_content_fingerprint available in knowledge-ingest**
Either:
- (A) Import `compute_content_fingerprint` from `klai-connector` (not possible — separate
  service), OR
- (B) Copy the pure function to `knowledge-ingest/knowledge_ingest/fingerprint.py` (small
  duplication but independent deploy), OR
- (C) Add a `POST /api/v1/compute-fingerprint` endpoint on klai-connector and call it
  from knowledge-ingest during preview.

**Recommended: option B** — `compute_content_fingerprint()` and `similarity()` are pure
functions (~30 lines total, no I/O). Copying them avoids cross-service HTTP calls during
the interactive preview flow. Add `trafilatura>=2.0` to knowledge-ingest's deps (it's
already indirectly available via crawl4ai's transitive deps, but pin explicitly).

**REQ-8: Edit connector loads auto-detected auth guard**
The edit-connector page SHALL load existing `canary_url` and `login_indicator_selector`
from the connector config. If set, the "Auth protection enabled" status line is shown
(same as step 3). The advanced fields are pre-filled and editable.

When the admin clears both fields and saves, the auth guard is disabled. The backend
clears `canary_fingerprint` when `canary_url` is removed (existing XOR validator handles
this).

### Recompute Fingerprint on Canary URL Change

**REQ-9: Portal calls klai-connector to recompute fingerprint when canary_url changes**
WHEN an admin manually changes the canary URL in advanced settings, the fingerprint must
be recomputed. This requires a crawl with cookies — which only klai-connector can do.

New endpoint: `POST /api/v1/compute-fingerprint` on klai-connector:
```json
// Request
{ "url": "https://wiki.example.com/new-page", "cookies": [...] }
// Response 200
{ "fingerprint": "abc123def4567890", "word_count": 142 }
// Response 422 (page too short)
{ "error": "page_too_short", "detail": "Page has fewer than 20 words" }
```

The endpoint uses `_post_crawl_sync()` (shared crawl helper from SPEC-CRAWL-003 refactor)
and `compute_content_fingerprint()`. Requires portal caller secret.

The portal backend calls this endpoint on connector save when `canary_url` changed.
If computation fails, the connector is saved without canary (both fields cleared),
and a warning is returned to the frontend.

---

## Acceptance Criteria

### AC-1: Preview with cookies auto-detects auth guard
**Given** a preview crawl with cookies that returns >100 words
**When** the preview response arrives
**Then** `auth_guard` is populated with `canary_url`, `canary_fingerprint`, and
  (if detectable) `login_indicator_selector`
**And** the UI shows "✓ Auth protection enabled" with a human-readable description.

### AC-2: Preview without cookies has no auth guard
**Given** a preview crawl without cookies
**When** the preview response arrives
**Then** `auth_guard` is `null`
**And** no auth protection UI is shown.

### AC-3: Advanced settings allow manual override
**Given** auto-detected auth guard values
**When** admin clicks "Advanced settings"
**Then** editable fields show the detected canary URL and login indicator
**And** the admin can change or clear them before saving.

### AC-4: Auto-detected values saved with connector
**Given** auth guard was auto-detected during preview
**When** the connector is saved without opening advanced settings
**Then** the config includes `canary_url`, `canary_fingerprint`, and
  `login_indicator_selector` from the auto-detection.

### AC-5: Manual canary URL change triggers recompute
**Given** admin manually changes canary URL in advanced settings
**When** the connector is saved
**Then** the portal calls klai-connector to compute the new fingerprint
**And** the new fingerprint is stored in the config.

### AC-6: Login indicator detection failure is non-blocking
**Given** the AI cannot identify a login indicator element
**When** the preview completes
**Then** `login_indicator_selector` is `null`
**And** the UI shows "✓ Auth protection enabled (canary check only)"
**And** Layer B stays disabled, Layer A still works.

### AC-7: Edit connector shows existing auth guard
**Given** a connector with auth guard configured
**When** the edit page loads
**Then** "Auth protection enabled" is shown with the saved values
**And** advanced settings show the current canary URL and selector.

### AC-8: Backward compat — no cookies, no auth guard
**Given** a connector without cookies
**When** saved and synced
**Then** behavior is identical to pre-SPEC-CRAWL-004
**And** Layer C still runs automatically.

---

## Implementation Notes

### Files to Change

**knowledge-ingest (2-3 files):**
- `knowledge_ingest/routes/crawl.py` — extend `CrawlPreviewResponse` with `auth_guard`;
  compute fingerprint from `fit_md`; call login indicator AI detection.
- `knowledge_ingest/fingerprint.py` — NEW, copy of `compute_content_fingerprint()` +
  `similarity()` (~30 lines, pure functions). Pin `trafilatura>=2.0` in deps.
- `knowledge_ingest/routes/crawl.py` or new `auth_detection.py` —
  `detect_login_indicator_via_llm()` function (mirrors `detect_selector_via_llm()`).

**klai-connector (1 file):**
- `klai-connector/app/routes/fingerprint.py` — NEW endpoint `POST /api/v1/compute-fingerprint`
  for manual canary URL changes. Uses `_post_crawl_sync()` + `compute_content_fingerprint()`.

**klai-portal backend (2 files):**
- `klai-portal/backend/app/api/connectors.py` or `app_knowledge_bases.py` — on connector
  save, if `canary_url` changed → call klai-connector compute-fingerprint endpoint.
- `klai-portal/backend/app/services/klai_connector_client.py` — add
  `compute_fingerprint(url, cookies)` method.

**klai-portal frontend (3 files):**
- `$kbSlug_.add-connector.tsx` — parse `auth_guard` from preview response; show
  confirmation + advanced toggle; include auth guard fields in save payload.
- `$kbSlug_.edit-connector.$connectorId.tsx` — same auth guard display for existing config.
- `paraglide/messages/` — i18n keys for confirmation text and advanced labels.

### UX — Preview Step After Successful Crawl

```
┌─────────────────────────────────────────────────┐
│ 3  Preview                                      │
├─────────────────────────────────────────────────┤
│ Preview URL                                     │
│ [https://wiki.redcactus.cloud/nl/phone/3cx___]  │
│                                                 │
│ Authentication cookies                          │
│ [session=eyJhb...; XSRF-TOKEN=eyJp...________] │
│                                                 │
│ [RUN PREVIEW]                                   │
│                                                 │
│ ┌─────────────────────────────────────────────┐ │
│ │ ## 3CX Plugin                               │ │
│ │ Met de 3CX plugin koppel je jouw ...        │ │
│ │ (preview content)                           │ │
│ └─────────────────────────────────────────────┘ │
│                                                 │
│ ✓ Auth protection enabled                       │
│   Checks "3CX Plugin" before every sync.        │
│   Pages without login menu excluded.             │
│   ⚙ Advanced settings                           │
│                                                 │
│                              [NEXT]    [BACK]   │
└─────────────────────────────────────────────────┘
```

Compare with the content selector AI detection — same UX language: the system tells you
what it found, and you can override if needed.

---

## Test Plan

| Test | What to verify |
|---|---|
| `test_preview_with_cookies_returns_auth_guard` | Preview response has `auth_guard` with canary + selector |
| `test_preview_without_cookies_no_auth_guard` | Preview response `auth_guard` is null |
| `test_preview_low_word_count_no_canary` | Preview <100 words → no `canary_url` in auth_guard |
| `test_login_indicator_ai_detection` | DOM summary → LLM returns CSS selector for logout button |
| `test_login_indicator_ai_failure_non_blocking` | LLM returns nothing → `login_indicator_selector` is null |
| `test_fingerprint_computed_from_fit_markdown` | `canary_fingerprint` matches `compute_content_fingerprint(fit_md)` |
| `test_auth_guard_saved_with_connector` | Connector config has all three fields after save |
| `test_manual_canary_change_triggers_recompute` | Changed URL → POST compute-fingerprint called |
| `test_compute_fingerprint_endpoint_success` | Returns 200 with 16-char hex + word_count |
| `test_compute_fingerprint_endpoint_auth` | Returns 403 without portal caller secret |
| `test_edit_connector_loads_auth_guard` | Edit page shows existing auth guard values |
| `test_backward_compat_no_cookies` | Connector without cookies → no auth guard fields |

---

## References

- SPEC-CRAWL-003: Three-layer content quality guardrails (deployed 2026-04-16)
- SPEC-CRAWL-001: AI-assisted content selector detection — `detect_selector_via_llm()` pattern
- `knowledge-ingest/routes/crawl.py` — existing preview endpoint + AI selector flow
- `klai-connector/app/services/content_fingerprint.py` — pure functions to copy
- `klai-connector/app/adapters/webcrawler.py` — `_post_crawl_sync()` shared crawl helper
- `.claude/rules/klai/projects/portal-backend.md` — portal patterns
