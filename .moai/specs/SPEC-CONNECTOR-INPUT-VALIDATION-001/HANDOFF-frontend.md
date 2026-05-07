# SPEC-CONNECTOR-INPUT-VALIDATION-001 — Frontend Handoff

**Status as of 2026-05-07:** Backend (REQ-2, REQ-3, REQ-4, REQ-1 backend half, REQ-7 backend half) shipped on branch `feature/SPEC-CONNECTOR-INPUT-VALIDATION-001` (worktree `/Users/mvletter/Developer/klai-connector-validation`).

**Frontend remaining:** REQ-1 wizard rewire + REQ-5 InvestigateDialog/badge. Both depend exclusively on the contracts that are now live in this branch.

---

## What's already wired (verify these contracts before frontend work)

### Backend endpoint: `POST /api/app/knowledge-bases/{kb_slug}/connectors/auth-probe`

Request:
```json
{
  "url": "https://wiki.redcactus.cloud/nl/45-bubble-api-van-derden",
  "cookies": [{"name": "prod-knowledgebase-session", "value": "..."}]
}
```

Response (5 outcomes):
```json
{
  "classification": "auth_ok | auth_failed_no_cookies | auth_failed_still_walled | auth_failed_credentials_invalid | auth_failed_unreachable",
  "match_reasons": ["http_unauthenticated", "session_cookie_minimal_body", "end_of_body_login_marker", ...],
  "word_count": 600,
  "auth_guard": {
    "canary_url": "...",
    "canary_fingerprint": "..."
  }
}
```

Owner role required. Cookies pass through to the wizard's existing `parseCookies()` then through to klai-knowledge-ingest.

### Backend endpoint extension: `POST /api/app/knowledge-bases/{kb_slug}/connectors/crawl-preview`

Existing endpoint, now also returns:
```json
{
  "classification": "success | selector_required | selector_returns_empty | requires_javascript | auth_wall_detected",
  "classification_reason": "80% of the text is links. Configure a Content Selector."
}
```

The wizard MUST gate the "Add connector" button on `classification === "success"`.

### `ConnectorOut.needs_reconfiguration: bool`

Already returned by `GET /api/app/knowledge-bases/{kb_slug}/connectors/`. True when the connector is a `web_crawler` and `last_sync_status === 'failed'`. Use this to render the red "Needs reconfiguration" badge.

---

## Frontend work plan

### REQ-1 — Wizard reorder (web_crawler only)

**Files:**
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.add-connector.tsx`
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug_.edit-connector.$connectorId.tsx`

**State machine:**

```typescript
type WizardState = {
  step: 1 | 2 | 3 | 4 | 5 | 6
  details: { name: string; baseUrl: string; pathPrefix: string }
  authQuestion: 'public' | 'private' | null
  cookies: string  // raw textarea, parsed via existing parseCookies()
  authProbeResult: AuthProbeResponse | null
  contentSelector: string
  previewResult: CrawlPreviewResponse | null
}

function canAdvanceFrom(step: WizardStep, state: WizardState): boolean {
  switch (step) {
    case 2: return Boolean(state.details.baseUrl && state.details.name)
    case 3: return state.authQuestion !== null
    case 4: return state.authProbeResult?.classification === 'auth_ok'
    case 5: return state.previewResult?.classification === 'success'
  }
  return true
}
```

**Cache invalidation (D-10):** `authProbeResult = null` whenever `baseUrl | pathPrefix | cookies` changes. `previewResult = null` whenever `baseUrl | pathPrefix | cookies | contentSelector` changes.

**Step 4 render rules** (per `classification`):
- `auth_ok` — green check, "You're in. Continue to Selector."
- `auth_failed_no_cookies` — "This page requires authentication. Go back and answer Yes to step 3."
- `auth_failed_still_walled` — "Cookies didn't unlock the content. Re-paste a fresh session cookie. Detected: {match_reasons.join(', ')}"
- `auth_failed_credentials_invalid` — "401/403 — credentials rejected."
- `auth_failed_unreachable` — "Could not reach the page. Check the Base URL."

**Step 5 render rules** (per `classification`):
- `success` — green check, "Add connector" button enables.
- `selector_required` — show `classification_reason` verbatim.
- `selector_returns_empty` — "Selector matched no content. Try a different selector or click 'Let AI find'."
- `requires_javascript` — "Page renders via JavaScript. Configure a wait_for or selector for the post-render DOM."
- `auth_wall_detected` — "This page requires authentication. Go back to step 4." Auto-jump to step 4.

**Edit-route step deep-link (D-11):**

```typescript
// $kbSlug_.edit-connector.$connectorId.tsx
const search = useSearch({ from: '/_app/app/knowledge/$kbSlug_/edit-connector/$connectorId' })
const initialStep = search.step === 'auth' ? 4 : search.step === 'selector' ? 5 : 1
```

Add `step: z.enum(['auth', 'selector']).optional()` to the route's search-param schema.

### REQ-5 — Connectors list error state

**File:** `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` (the connectors table component).

**Render rules:**
- When `connector.needs_reconfiguration === true`: render red `Badge` with text "Needs reconfiguration".
- Click badge OR explicit "Investigate" link → open `<InvestigateDialog connectorId={c.id} kbSlug={kbSlug} />`.

**InvestigateDialog (new component):**
- Title: "Connector needs reconfiguration"
- Body: explanatory text from a static map (the actual `error_details.suggestion` will become available once the cross-DB join lands).
- Three buttons:
  - "Edit Authentication" → `navigate({ to: '/app/knowledge/$kbSlug/edit-connector/$connectorId', search: { step: 'auth' }, params: { kbSlug, connectorId: c.id } })`
  - "Edit Selector" → same with `step: 'selector'`
  - "Run Preview" → `step: 'selector'` + `?autoPreview=1` (the wizard's onMount inspects this and triggers the preview button programmatically)

### REQ-6 — Frontend tests

**Files (new):**
- `klai-portal/frontend/src/routes/app/knowledge/__tests__/wizard-add-connector.test.tsx`
- `klai-portal/frontend/src/routes/app/knowledge/__tests__/wizard-edit-connector.test.tsx`
- `klai-portal/frontend/src/routes/app/knowledge/__tests__/connectors-list-error-state.test.tsx`

**Test list:**
- `test_wizard_cannot_skip_auth_step` — fill details, attempt step-5 advance → blocked.
- `test_wizard_disabled_add_button_until_step_5_success`.
- `test_wizard_re_verifies_when_base_url_changed_after_auth_ok`.
- `test_wizard_shows_match_reasons_on_auth_failed_still_walled`.
- `test_wizard_shows_match_reasons_on_selector_required` (Redcactus case).
- `test_edit_route_step_param_opens_at_correct_step` — `?step=auth` → wizard at 4.
- `test_edit_pre_populates_state_from_existing_connector`.
- `test_connectors_list_shows_failed_badge_for_dirty_content_reason`.
- `test_investigate_dialog_renders_suggestion_text`.
- `test_edit_authentication_button_navigates_with_step_param`.

Use Vitest + Testing Library. Mock `fetch` with the contracts above.

---

## Acceptance verification (post-frontend)

When the frontend lands, run these against the live Voys/support tenant:

1. **AC-2** — Configure web_crawler at the Redcactus URL with step 3 = "No, public". REQ-2 returns `auth_failed_no_cookies`; wizard refuses to advance.
2. **AC-3** — Same URL, step 3 = "Yes" + valid cookie. REQ-2 returns `auth_ok`. Wizard advances.
3. **AC-5** — Public site, no `content_selector`. REQ-3 returns `selector_required`.
4. **AC-7** — Trigger sync on a connector whose seed is now auth-walled. Sync ends `failed` with `error_summary.reason='boilerplate_or_authwall_dominant'` (REQ-4 backend already does this).
5. **AC-8** — Connectors list shows red badge for the failed Redcactus connector. "Edit Authentication" navigates to `?step=auth`.
6. **AC-9** — Existing Voys/support Redcactus connector (id `e7fac358-…`) flagged on first list-page view post-deploy.

---

## Trade-offs to flag

1. **`needs_reconfiguration` proxy** — the SPEC's full predicate (`error_details.reason == 'boilerplate_or_authwall_dominant'`) requires querying `connector.sync_runs` in klai-connector's schema. The proxy `connector_type == 'web_crawler' AND last_sync_status == 'failed'` is correct for AC-9 (the Redcactus connector currently has `last_sync_status='failed'`) but will surface false positives if a web_crawler fails for unrelated reasons. The "Investigate" dialog's deep-links into the wizard will surface the real problem on the next user click — so false positives have a recovery path; not a hard blocker for shipping.

2. **`error_details.suggestion` text** — REQ-4 writes this server-side, but portal does not currently fetch it (would require either a sync-status callback extension to persist `error_summary` JSONB on `portal_connectors`, OR an HTTP round-trip per connector to klai-connector's `/sync-runs?limit=1` endpoint). For MVP frontend, a static map of suggestion text per `last_sync_status` is acceptable; the live `error_summary` integration is a follow-up SPEC.

3. **REQ-6 backend integration test for `crawl_site` dirty-content guard** — covered by 8 pure-function unit tests in `tests/test_crawl_site_dirty_content_guard.py`. A full integration test would require a live crawl4ai container and is out of scope for unit-test CI; the integration is one call-site in `run_crawl_job` with no branching logic.

---

## Branch / commits

```
feature/SPEC-CONNECTOR-INPUT-VALIDATION-001 (worktree at /Users/mvletter/Developer/klai-connector-validation)
├── 33af111d  feat(ingest): REQ-2 — auth-wall classifier + /auth-probe endpoint
├── a55a4cf8  feat(ingest): REQ-3 — link-density helper + preview classification
├── ffd87ad5  feat(ingest): REQ-4 — sync-time hard-fail on dirty content
└── ff4ded8e  feat(portal): REQ-1/7 backend — auth-probe pass-through + needs_reconfiguration
```

Test totals shipped this branch:
- 41 auth_wall_classifier unit tests
- 12 auth-probe endpoint tests
- 9 link_density unit tests
- 7 preview-classification tests
- 12 dirty-content guard unit tests
- 8 needs_reconfiguration predicate tests
- 1 neighbor regression fix

**~90 net new tests, all green; pre-existing main-branch failures (test_crawl_url_ssrf_parity, test_request_with_correct_secret_passes) untouched.**
