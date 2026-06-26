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
import type { AuthProbeResult, PreviewClassification } from './-connector-types'

/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 - render auth-probe outcome.
 * Shared by add-connector and edit-connector flows.
 */
export function AuthProbeFeedback({ result }: { result: AuthProbeResult }) {
  if (result.classification === 'auth_ok') {
    return (
      <Alert variant="success" size="sm">
        <span>You&apos;re in. Continue to Selector.</span>
      </Alert>
    )
  }
  const reasons = result.match_reasons.length > 0
    ? ` Detected: ${result.match_reasons.join(', ')}`
    : ''
  let message: string
  switch (result.classification) {
    case 'auth_failed_no_cookies':
      message = 'This page requires authentication. Go back to step 3 and answer Yes.'
      break
    case 'auth_failed_still_walled':
      message = `Cookies didn't unlock the content. Re-paste a fresh session cookie.${reasons}`
      break
    case 'auth_failed_credentials_invalid':
      message = '401/403 - credentials rejected.'
      break
    case 'auth_failed_unreachable':
      message = 'Could not reach the page. Check the Base URL.'
      break
    default:
      message = `Authentication check failed.${reasons}`
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
        <span>Selector matches real article content. You can save the connector.</span>
      </Alert>
    )
  }
  let message: string
  switch (classification) {
    case 'selector_required':
      message =
        reason ?? 'The output looks like a navigation menu. Configure a Content Selector.'
      break
    case 'selector_returns_empty':
      message =
        reason ?? "Selector matched no content. Try a different selector or click 'Let AI find'."
      break
    case 'requires_javascript':
      message =
        'Page renders via JavaScript. Configure a wait_for condition or selector for the post-render DOM.'
      break
    case 'auth_wall_detected':
      message = 'This page requires authentication. Go back to step 4.'
      break
    case 'unknown':
      message = reason ?? 'Preview service did not respond. Try again.'
      break
    default:
      message = reason ?? 'Selector check failed.'
  }
  return (
    <div className="space-y-2">
      <Alert variant="warning" size="sm">
        <span>{message}</span>
      </Alert>
      {classification === 'unknown' && onRetry && (
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors"
          onClick={onRetry}
        >
          Retry
        </button>
      )}
    </div>
  )
}
