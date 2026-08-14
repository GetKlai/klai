// Shared constants and tiny helpers for the connector wizard pages.
// Companion to `-connector-types.ts`. Per the
// "File organization for shared types and helpers" rule
// (.claude/rules/klai/projects/portal-frontend.md).
//
// `joinSeedUrl` is a one-function helper kept here rather than in its own
// `-connector-helpers.ts` because a single function does not warrant a
// separate file (would be the proliferation anti-pattern the rule warns
// against). When a second wizard-only helper appears, split this file.

import { type MultiSelectOption } from '@/components/ui/multi-select'
import type { ConnectorType, StepDeepLink } from './-connector-types'

// Tailwind class string applied to the markdown preview pane in both
// wizards. Kept as a single source of truth so style changes don't
// silently diverge between add and edit views.
export const MARKDOWN_PROSE_CLASSES =
  'overflow-y-auto max-h-64 text-xs [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-gray-900 [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-gray-900 [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-gray-900 [&_h3]:mb-1 [&_p]:text-gray-400 [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-gray-400 [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-gray-400 [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-gray-900 [&_hr]:border-gray-200 [&_hr]:my-2'

// Valid `?type=` URL-param values for the add-connector route. Used
// by the route's validateSearch to gate the deep-link.
export const VALID_PRESELECT_TYPES = new Set<ConnectorType>([
  'github', 'notion', 'google_drive', 'google_docs', 'google_sheets', 'google_slides',
  'airtable', 'confluence', 'ms_docs', 'web_crawler',
])

export function normalizeConnectorPreselectType(type?: ConnectorType): ConnectorType | undefined {
  if (type === 'google_docs' || type === 'google_sheets' || type === 'google_slides') {
    return 'google_drive'
  }
  return type
}

// Valid `?step=` URL-param values for the edit-connector route. Used
// by the route's validateSearch to deep-link into the auth-setup or
// selector wizard step.
export const VALID_STEPS = new Set<StepDeepLink>(['auth', 'selector'])

// SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-9: assertion-mode options
// shown in the wizard's allowedAssertionModes MultiSelect.
export const ASSERTION_MODE_OPTIONS: MultiSelectOption[] = [
  { value: 'factual',    label: 'Fact',        description: 'Established fact, documentation, specs' },
  { value: 'procedural', label: 'Procedure',   description: "Step-by-step instructions, how-to's" },
  { value: 'belief',     label: 'Claim',       description: 'Not conclusively proven claim' },
  { value: 'quoted',     label: 'Quote',       description: 'Literal source material' },
  { value: 'hypothesis', label: 'Speculation', description: 'Hypotheses, brainstorm' },
  { value: 'unknown',    label: 'Unknown',     description: 'Type not specified' },
]

/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 hotfix - slash-safe URL build.
 *
 * Combines ``base_url`` and ``path_prefix`` without producing the ``//``
 * artifact that crawl4ai handles inconsistently (`https://x.com/` + `/nl/`
 * = `https://x.com//nl/`). Trims trailing slash off base, leading slash
 * off path, then joins with single `/` if path is non-empty.
 *
 * Used by both add-connector and edit-connector wizard auth-probe call sites.
 */
export function joinSeedUrl(baseUrl: string, pathPrefix: string): string {
  const base = baseUrl.replace(/\/+$/, '')
  const path = (pathPrefix || '').replace(/^\/+/, '').replace(/\/+$/, '')
  if (!path) return base + '/'
  return `${base}/${path}/`
}

/**
 * Is `url` the base URL itself, or a page below it?
 *
 * Boundary-aware on purpose: a bare `startsWith(base)` also accepts
 * `https://x.com.evil.test/...` for base `https://x.com`, which would let a
 * crawl seed wander off-site.
 */
export function isWithinBaseUrl(url: string, baseUrl: string): boolean {
  const base = baseUrl.replace(/\/+$/, '')
  if (!base || !url) return false
  return url === base || url.startsWith(`${base}/`)
}

/**
 * Which preview URL to show when advancing past the details step.
 *
 * The preview field doubles as the crawl's discovery seed: the edit wizard
 * prefills it from the stored `discovery_seed_url`, and a validated non-base
 * URL is saved back as that seed. Advancing from details used to overwrite it
 * with the base URL unconditionally, so an operator editing a connector never
 * saw the interior page they had validated earlier (reported 2026-08-13).
 *
 * Keep whatever is in the field while it still belongs to this site; fall back
 * to the base URL when the field is empty or the operator edited `base_url` so
 * the previous value is now out of scope.
 */
export function previewUrlOnDetailsAdvance(currentPreviewUrl: string, baseUrl: string): string {
  const current = (currentPreviewUrl || '').trim()
  return isWithinBaseUrl(current, baseUrl) ? current : baseUrl
}
