/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-6 - wizard feedback components.
 *
 * Tests cover the pure render helpers ``AuthProbeFeedback`` (REQ-2)
 * and ``PreviewClassificationFeedback`` (REQ-3). These components are
 * shared between add-connector and edit-connector and live in
 * `-connector-feedback.tsx` (per the file-organization rule in
 * portal-frontend.md). Unit-testable in isolation - no router or
 * query-client setup needed.
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  AuthProbeFeedback,
  PreviewClassificationFeedback,
} from '../-connector-feedback'

describe('AuthProbeFeedback', () => {
  it('renders success state for auth_ok', () => {
    render(
      <AuthProbeFeedback
        result={{
          classification: 'auth_ok',
          match_reasons: [],
          word_count: 600,
          auth_guard: null,
        }}
      />,
    )
    expect(screen.getByText(/you're in/i)).toBeTruthy()
  })

  it('tells user to go back to step 3 on auth_failed_no_cookies', () => {
    render(
      <AuthProbeFeedback
        result={{
          classification: 'auth_failed_no_cookies',
          match_reasons: ['end_of_body_login_marker'],
          word_count: 80,
          auth_guard: null,
        }}
      />,
    )
    expect(screen.getByText(/go back to step 3 and answer yes/i)).toBeTruthy()
  })

  it('exposes match_reasons on auth_failed_still_walled', () => {
    render(
      <AuthProbeFeedback
        result={{
          classification: 'auth_failed_still_walled',
          match_reasons: ['session_cookie_minimal_body', 'end_of_body_login_marker'],
          word_count: 10,
          auth_guard: null,
        }}
      />,
    )
    expect(
      screen.getByText(/session_cookie_minimal_body/),
    ).toBeTruthy()
    expect(screen.getByText(/end_of_body_login_marker/)).toBeTruthy()
  })

  it('shows credentials rejected message on 401/403', () => {
    render(
      <AuthProbeFeedback
        result={{
          classification: 'auth_failed_credentials_invalid',
          match_reasons: ['http_unauthenticated'],
          word_count: 0,
          auth_guard: null,
        }}
      />,
    )
    expect(screen.getByText(/401\/403/i)).toBeTruthy()
  })

  it('shows base-url hint on auth_failed_unreachable', () => {
    render(
      <AuthProbeFeedback
        result={{
          classification: 'auth_failed_unreachable',
          match_reasons: [],
          word_count: 0,
          auth_guard: null,
        }}
      />,
    )
    expect(screen.getByText(/check the base url/i)).toBeTruthy()
  })
})

describe('PreviewClassificationFeedback', () => {
  it('renders success message and references saving the connector', () => {
    render(<PreviewClassificationFeedback classification="success" reason={null} />)
    expect(screen.getByText(/you can save the connector/i)).toBeTruthy()
  })

  it('shows the server-supplied reason for selector_required', () => {
    render(
      <PreviewClassificationFeedback
        classification="selector_required"
        reason="80% of the text is links. Configure a Content Selector."
      />,
    )
    expect(
      screen.getByText(/80% of the text is links/i),
    ).toBeTruthy()
  })

  it('shows AI-find suggestion for selector_returns_empty', () => {
    render(
      <PreviewClassificationFeedback
        classification="selector_returns_empty"
        reason={null}
      />,
    )
    expect(screen.getByText(/let ai find/i)).toBeTruthy()
  })

  it('shows JS hint for requires_javascript', () => {
    render(
      <PreviewClassificationFeedback
        classification="requires_javascript"
        reason={null}
      />,
    )
    expect(screen.getByText(/javascript/i)).toBeTruthy()
  })

  it('redirects user to step 4 on auth_wall_detected', () => {
    render(
      <PreviewClassificationFeedback
        classification="auth_wall_detected"
        reason={null}
      />,
    )
    expect(screen.getByText(/go back to step 4/i)).toBeTruthy()
  })

  it('PreviewClassificationFeedback renders unknown classification with reason text', () => {
    render(
      <PreviewClassificationFeedback
        classification="unknown"
        reason="Preview service did not respond. Try again."
      />,
    )
    expect(screen.getByText(/preview service did not respond/i)).toBeTruthy()
  })

  it('PreviewClassificationFeedback renders unknown classification offers retry context', () => {
    const handleRetry = () => undefined
    render(
      <PreviewClassificationFeedback
        classification="unknown"
        reason={null}
        onRetry={handleRetry}
      />,
    )
    // Retry button should appear when onRetry is provided
    expect(screen.getByText(/retry/i)).toBeTruthy()
  })
})
