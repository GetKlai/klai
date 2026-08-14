/** Segment shape needed to compute a display-relative timestamp. */
export interface TranscriptSegmentLike {
  start: number
  absolute_start_time?: string | null
}

const MAX_DISPLAYABLE_SECONDS = 24 * 60 * 60

/**
 * Elapsed seconds for a transcript segment, relative to the meeting.
 *
 * Vexa's raw `start` field is relative to the underlying bot recording's
 * own internal clock, not to this specific meeting -- when a bot session
 * runs far longer than expected (see the "Fix Vexa lifecycle" incident),
 * `start` can be a huge, meaningless offset.
 *
 * When both the segment's `absolute_start_time` (a real UTC timestamp)
 * and the meeting's `started_at` are available, elapsed time is derived
 * from those wall-clock values instead, sidestepping any drift in
 * Vexa's own clock. Otherwise this falls back to the segment's own
 * `start` minus the first segment's `start` -- still the same unit, so
 * always self-consistent even without wall-clock data.
 */
export function relativeSegmentSeconds(
  segment: TranscriptSegmentLike,
  meetingStartedAt: string | null,
  firstSegmentStart: number,
): number {
  if (meetingStartedAt && segment.absolute_start_time) {
    const meetingStart = Date.parse(meetingStartedAt)
    const segStart = Date.parse(segment.absolute_start_time)
    if (Number.isFinite(meetingStart) && Number.isFinite(segStart)) {
      return (segStart - meetingStart) / 1000
    }
  }
  return segment.start - firstSegmentStart
}

/**
 * Format elapsed seconds as `m:ss` (or `h:mm:ss` once past the hour).
 *
 * Returns `null` for negative or absurdly large (>= 24h) input rather
 * than rendering a nonsensical timestamp -- a bad upstream `start`
 * value should hide the timestamp, not display garbage.
 */
export function formatSegmentTimestamp(seconds: number): string | null {
  if (!Number.isFinite(seconds) || seconds < 0 || seconds >= MAX_DISPLAYABLE_SECONDS) {
    return null
  }
  const h = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  if (h > 0) {
    return `${h}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`
}
