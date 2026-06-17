import { describe, expect, it } from 'vitest'

import { activeMeetingInfoKind } from '../-status-copy'

describe('activeMeetingInfoKind', () => {
  it('does not use admission copy for stopping meetings', () => {
    expect(activeMeetingInfoKind('stopping')).toBe('stopping')
  })

  it('uses processing copy for processing meetings', () => {
    expect(activeMeetingInfoKind('processing')).toBe('processing')
  })

  it('uses active copy before the meeting ends', () => {
    expect(activeMeetingInfoKind('joining')).toBe('active')
    expect(activeMeetingInfoKind('recording')).toBe('active')
  })
})
