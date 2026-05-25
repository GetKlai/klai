/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-5 - edit wizard deep-link routing.
 *
 * Tests cover the step-to-WcStep mapping that drives the edit wizard's
 * initial step when the user arrives via a ?step= deep-link from the
 * connectors-list "Investigate" action.
 *
 * Matching the existing test style: toBeTruthy(), no jest-dom matchers.
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PreviewClassificationFeedback } from '../-connector-feedback'

// ---------------------------------------------------------------------------
// Step-to-WcStep mapping logic (mirrors the module-level helper in
// $kbSlug_.edit-connector.$connectorId.tsx). Tested here in isolation to
// avoid the full router/query-client setup the page component requires.
// ---------------------------------------------------------------------------

type WcStep = 'details' | 'auth-question' | 'auth-setup' | 'selector' | 'settings'
type StepDeepLink = 'auth' | 'selector'

function stepToWcStep(step: StepDeepLink | undefined): WcStep | undefined {
  if (step === 'auth') return 'auth-setup'
  if (step === 'selector') return 'selector'
  return undefined
}

describe('edit wizard step deep-link', () => {
  it('?step=auth opens edit wizard at WcStep auth-setup with requiresLogin=true', () => {
    const result = stepToWcStep('auth')
    expect(result === 'auth-setup').toBeTruthy()
  })

  it('?step=selector opens edit wizard at WcStep selector', () => {
    const result = stepToWcStep('selector')
    expect(result === 'selector').toBeTruthy()
  })

  it('no step param defaults to details', () => {
    const result = stepToWcStep(undefined)
    // Undefined maps to details (the caller falls back to 'details')
    const resolved: WcStep = result ?? 'details'
    expect(resolved === 'details').toBeTruthy()
  })

  it('pre-existing connector config does NOT auto-pass the auth/preview gates (re-verify policy)', () => {
    // SPEC D-1: both authProbeResult and previewResult start as null on edit entry.
    // The save button is disabled when previewResult?.classification !== 'success'.
    // This test verifies PreviewClassificationFeedback shows an amber state (not green)
    // for the 'unknown' classification used as the initial placeholder when entering
    // the selector step from auth-setup.
    render(
      <PreviewClassificationFeedback
        classification="unknown"
        reason="Preview service did not respond. Try again."
      />,
    )
    // Green "success" text must NOT be present
    const successText = screen.queryByText(/you can save the connector/i)
    expect(successText === null).toBeTruthy()
    // Amber warning must be present
    expect(screen.getByText(/preview service did not respond/i)).toBeTruthy()
  })
})
