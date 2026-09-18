// Shared render helpers for the connector wizard pages.
// Companion to `-connector-types.ts` and `-connector-constants.ts`.
// Per the "File organization for shared types and helpers" rule
// (.claude/rules/klai/projects/portal-frontend.md).
//
// These two components are pure functions of their props (no internal
// state, no side effects). They render the structured feedback for the
// wizard's two probe outcomes:
//   - AuthProbeFeedback: REQ-2 - auth-probe classification + reasons.
//   - PreviewClassificationFeedback: REQ-3 - preview-pipeline judgement.
//
// Both are tested directly via __tests__/wizard-feedback.test.tsx (no
// router/query-client setup required).

import { Alert } from '@/components/ui/alert'
import * as m from '@/paraglide/messages'
import type { AuthProbeResult, PreviewClassification } from './-connector-types'

/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 - render auth-probe outcome.
 * Shared by add-connector and edit-connector flows.
 */
export function AuthProbeFeedback({ result }: { result: AuthProbeResult }) {
  if (result.classification === 'auth_ok') {
    return (
      <Alert variant="success" size="sm">
        <span>{m.admin_connectors_webcrawler_auth_probe_ok()}</span>
      </Alert>
    )
  }
  const reasons = result.match_reasons.length > 0
    ? ` ${m.admin_connectors_webcrawler_auth_probe_detected({ reasons: result.match_reasons.join(', ') })}`
    : ''
  let message: string
  switch (result.classification) {
    case 'auth_failed_no_cookies':
      message = m.admin_connectors_webcrawler_auth_probe_no_cookies()
      break
    case 'auth_failed_still_walled':
      message = `${m.admin_connectors_webcrawler_auth_probe_still_walled()}${reasons}`
      break
    case 'auth_failed_credentials_invalid':
      message = m.admin_connectors_webcrawler_auth_probe_credentials_invalid()
      break
    case 'auth_failed_unreachable':
      message = m.admin_connectors_webcrawler_auth_probe_unreachable()
      break
    default:
      message = `${m.admin_connectors_webcrawler_auth_probe_failed()}${reasons}`
  }
  return (
    <Alert variant="warning" size="sm">
      <span>{message}</span>
    </Alert>
  )
}

/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 - render preview classification outcome.
 * Single source of truth for all classification-driven feedback.
 * Supporting affordances (markdown body, AI selector, auth-guard) compose alongside
 * via the parent - this component only renders the primary message.
 */
export function PreviewClassificationFeedback({
  classification,
  reason,
  onRetry,
}: {
  classification: PreviewClassification
  reason: string | null
  onRetry?: () => void
}) {
  if (classification === 'success') {
    return (
      <Alert variant="success" size="sm">
        {/* When the verdict came from the site sample, `reason` explains that the
            entry page is navigation while the pages behind it hold content. Without
            it a green check above an empty preview body reads as a bug. */}
        <span>{reason ?? m.admin_connectors_webcrawler_preview_success_default()}</span>
      </Alert>
    )
  }
  let message: string
  switch (classification) {
    case 'selector_required':
      message =
        reason ?? m.admin_connectors_webcrawler_preview_selector_required()
      break
    case 'selector_returns_empty':
      message =
        reason ?? m.admin_connectors_webcrawler_preview_selector_empty()
      break
    case 'requires_javascript':
      message = m.admin_connectors_webcrawler_preview_requires_js()
      break
    case 'entry_point_empty':
      // The reason from the backend already spells out the fix (paste a
      // specific deeper URL); fall back defensively if it is ever absent.
      message =
        reason ?? m.admin_connectors_webcrawler_preview_entry_empty()
      break
    case 'auth_wall_detected':
      message = m.admin_connectors_webcrawler_preview_auth_wall()
      break
    case 'unknown':
      message = reason ?? m.admin_connectors_webcrawler_preview_unknown()
      break
    default:
      message = reason ?? m.admin_connectors_webcrawler_preview_selector_check_failed()
  }
  return (
    <div className="space-y-2">
      <Alert variant="warning" size="sm">
        <span>{message}</span>
      </Alert>
      {/* The check is advice, not a verdict. A crawl that looks poor on the entry
          page can still index the site, and the user — not the preview — decides
          whether this source belongs in their knowledge base. Suppressed for
          entry_point_empty: there the crawl would index nothing, so the fix is a
          different URL, not "save anyway". */}
      {classification !== 'auth_wall_detected' &&
        classification !== 'unknown' &&
        classification !== 'entry_point_empty' && (
          <p className="text-xs text-gray-500">
            {m.admin_connectors_webcrawler_preview_can_still_save()}
          </p>
        )}
      {classification === 'unknown' && onRetry && (
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 transition-colors"
          onClick={onRetry}
        >
          {m.admin_connectors_webcrawler_preview_retry()}
        </button>
      )}
    </div>
  )
}
