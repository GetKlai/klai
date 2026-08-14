import { describe, expect, it } from 'vitest'

import { formatSegmentTimestamp, relativeSegmentSeconds } from '../-transcript-timestamp'

describe('formatSegmentTimestamp', () => {
  it('formats zero seconds', () => {
    expect(formatSegmentTimestamp(0)).toBe('0:00')
  })

  it('formats sub-hour durations as m:ss', () => {
    expect(formatSegmentTimestamp(65)).toBe('1:05')
  })

  it('formats durations past the hour as h:mm:ss', () => {
    expect(formatSegmentTimestamp(3661)).toBe('1:01:01')
  })

  it('hides absurdly large durations (>= 24h) instead of showing garbage', () => {
    // The 2026-08-14 production bug: a stale Vexa bot clock produced a
    // `start` value of 496306.28s (~137.9h) for a segment mid-meeting.
    expect(formatSegmentTimestamp(496306.28)).toBeNull()
    expect(formatSegmentTimestamp(24 * 60 * 60)).toBeNull()
  })

  it('hides negative durations instead of showing garbage', () => {
    expect(formatSegmentTimestamp(-5)).toBeNull()
  })

  it('hides non-finite input', () => {
    expect(formatSegmentTimestamp(Number.NaN)).toBeNull()
    expect(formatSegmentTimestamp(Number.POSITIVE_INFINITY)).toBeNull()
  })
})

describe('relativeSegmentSeconds', () => {
  it('derives elapsed time from absolute_start_time vs meeting.started_at when both are present', () => {
    const seconds = relativeSegmentSeconds(
      { start: 999999, absolute_start_time: '2026-08-14T10:05:30.000Z' },
      '2026-08-14T10:00:00.000Z',
      0,
    )
    expect(seconds).toBe(330)
  })

  it('falls back to start-minus-first-segment-start when absolute_start_time is missing', () => {
    const seconds = relativeSegmentSeconds({ start: 620 }, '2026-08-14T10:00:00.000Z', 500)
    expect(seconds).toBe(120)
  })

  it('falls back to start-minus-first-segment-start when meeting.started_at is missing', () => {
    const seconds = relativeSegmentSeconds(
      { start: 620, absolute_start_time: '2026-08-14T10:05:30.000Z' },
      null,
      500,
    )
    expect(seconds).toBe(120)
  })

  it('falls back when absolute_start_time is unparseable', () => {
    const seconds = relativeSegmentSeconds(
      { start: 620, absolute_start_time: 'not-a-date' },
      '2026-08-14T10:00:00.000Z',
      500,
    )
    expect(seconds).toBe(120)
  })

  it('reproduces the production bug scenario: stale Vexa clock produces a huge start value', () => {
    // Without a meeting-relative anchor, a bot reused across sessions can
    // report `start` as seconds-since-bot-boot rather than since this
    // meeting -- e.g. 496306.28s (~137.9h) for a segment actually a few
    // minutes into the meeting. Falling back to first-segment-start still
    // yields a sane elapsed time because both values share the same
    // (buggy) clock.
    const seconds = relativeSegmentSeconds({ start: 496306.28 }, null, 496300.0)
    expect(seconds).toBeCloseTo(6.28, 5)
    expect(formatSegmentTimestamp(seconds)).toBe('0:06')
  })
})
