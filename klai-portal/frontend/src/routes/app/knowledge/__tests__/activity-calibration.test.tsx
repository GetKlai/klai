/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §4.6/§4.7 (fase 3) — the calibration readout
 * panel. It renders straight off a `GET /api/app/activity/summary` payload,
 * so the panel is tested directly against that shape rather than through the
 * whole list-page harness (react-query, router, auth) `activity-index.test.tsx`
 * already covers.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CalibrationPanel } from '@/features/chat-activity/CalibrationPanel'
import type { ActivitySummary } from '@/features/chat-activity'

function summary(overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    reviewed: 84,
    by_band: [
      { band: 'high', reviewed: 40, correct: 34 },
      { band: 'low', reviewed: 10, correct: 3 },
    ],
    by_judge_outcome: [{ judge_outcome: 'resolved', reviewed: 30, human_correct: 27 }],
    by_judge_category: [{ judge_category: 'retrieval_miss', human_cause: 'knowledge_missing', count: 12 }],
    broad_mode: { reviewed: 9, correct: 5 },
    strict_on_gap: { reviewed: 20, correct: 7 },
    by_language: [{ language: 'nl', reviewed: 70, correct: 55 }],
    ...overrides,
  }
}

describe('CalibrationPanel', () => {
  it('renders the high-band sentence with the computed percentage and count', () => {
    render(<CalibrationPanel summary={summary()} />)

    // 34 correct of 40 reviewed high-band answers -> 85%, matched loosely so
    // the assertion holds for both nl and en copy ("85% van 40" / "85% of 40").
    expect(screen.getByText(/85%.*(van|of) 40/)).toBeTruthy()
  })

  it('keeps the band/judge/language tables behind a closed disclosure by default', () => {
    render(<CalibrationPanel summary={summary()} />)

    const details = document.querySelector('details')
    expect(details).not.toBeNull()
    expect(details?.open).toBe(false)
  })

  it('shows a single empty-state sentence instead of the panel when nothing was reviewed', () => {
    render(<CalibrationPanel summary={summary({ reviewed: 0 })} />)

    expect(document.querySelector('details')).toBeNull()
  })
})
