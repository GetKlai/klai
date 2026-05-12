// Shared constants for the connector wizard pages.
// Companion to `-connector-types.ts`. Per the
// "File organization for shared types and helpers" rule
// (.claude/rules/klai/projects/portal-frontend.md).

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

// Valid `?step=` URL-param values for the edit-connector route. Used
// by the route's validateSearch to deep-link into the auth-setup or
// selector wizard step.
export const VALID_STEPS = new Set<StepDeepLink>(['auth', 'selector'])
