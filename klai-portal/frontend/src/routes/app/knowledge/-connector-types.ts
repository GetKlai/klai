// Shared types for the connector wizard pages.
// Consumed by both `$kbSlug_.add-connector.tsx` and
// `$kbSlug_.edit-connector.$connectorId.tsx`. Per the
// "File organization for shared types and helpers" rule
// (.claude/rules/klai/projects/portal-frontend.md), wizard-specific
// shared types live at the parent route level — NOT in `$kbSlug/-kb-types.ts`
// (wrong scope: that file is for KB-tab routes inside `$kbSlug/`).
//
// Connector-types that are also used by KB-tab routes (e.g. `GitHubConfig`,
// `WebCrawlerConfig`, `CookieRow`, `ConnectorSummary`) live in
// `$kbSlug/-kb-types.ts` because they predate this split. A future SPEC
// (F-C2 in SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 § Follow-ups) may
// hoist them to a parent-level shared file too.

// SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-1: web_crawler wizard step order is
// Details → AuthQuestion → AuthSetup (only if requires login) → Selector → Settings.
// AuthSetup runs the REQ-2 auth-probe; Selector gates on the REQ-3 success
// classification. Other connector types (github/notion/...) are unaffected.
export type WcStep = 'details' | 'auth-question' | 'auth-setup' | 'selector' | 'settings'

// Edit-only deep-link contract: ?step=auth|selector navigates the user
// directly to the auth-setup or selector step on the edit wizard.
export type StepDeepLink = 'auth' | 'selector'

export type ConnectorType =
  | 'github' | 'web_crawler' | 'google_drive' | 'notion' | 'ms_docs'
  | 'airtable' | 'confluence'
  | 'google_docs' | 'google_sheets' | 'google_slides'

export type AuthProbeClassification =
  | 'auth_ok'
  | 'auth_failed_no_cookies'
  | 'auth_failed_still_walled'
  | 'auth_failed_credentials_invalid'
  | 'auth_failed_unreachable'

export interface AuthGuardSuggestion {
  canary_url: string | null
  canary_fingerprint: string | null
  login_indicator_selector: string | null
  login_indicator_description: string | null
}

export interface AuthProbeResult {
  classification: AuthProbeClassification
  match_reasons: string[]
  word_count: number
  auth_guard: AuthGuardSuggestion | null
}

export type PreviewClassification =
  | 'success'
  | 'selector_required'
  | 'selector_returns_empty'
  | 'requires_javascript'
  | 'auth_wall_detected'
  | 'unknown'

export type PreviewResult = {
  fit_markdown: string
  word_count: number
  warnings: string[]
  content_selector: string | null
  selector_source: string | null
  auth_guard: AuthGuardSuggestion | null
  classification: PreviewClassification
  classification_reason: string | null
}

// Per-connector form-state shapes.
//
// `AirtableConfig` and `ConfluenceConfig` are identical in add and edit;
// the previous `*EditConfig` names were just textual duplicates.
//
// `Notion*` is the exception: add takes a fresh `access_token`, edit
// takes an optional `new_access_token` (existing token is held by the
// connector record). The discriminated union `NotionConfig` exposes
// both shapes for shared consumers (e.g. a future
// `useConnectorWizardState` hook); each page keeps consuming its own
// branch directly.

export interface AirtableConfig {
  api_key: string
  base_id: string
  table_names: string
  view_name: string
}

export interface ConfluenceConfig {
  base_url: string
  email: string
  api_token: string
  space_keys: string
}

export interface NotionAddConfig {
  access_token: string
  database_ids: string
  max_pages: string
}

export interface NotionEditConfig {
  database_ids: string
  max_pages: string
  new_access_token: string
}

export type NotionConfig = NotionAddConfig | NotionEditConfig
