import { describe, it, expect } from 'vitest'
import { getPaginationRange } from '../pagination'

describe('getPaginationRange', () => {
  it('lists every page when the count fits without truncation', () => {
    expect(getPaginationRange(1, 7)).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(getPaginationRange(3, 5)).toEqual([1, 2, 3, 4, 5])
  })

  it('truncates only on the right near the start', () => {
    expect(getPaginationRange(1, 10)).toEqual([1, 2, 3, 4, 5, 'ellipsis-right', 10])
    expect(getPaginationRange(3, 10)).toEqual([1, 2, 3, 4, 5, 'ellipsis-right', 10])
  })

  it('truncates only on the left near the end', () => {
    expect(getPaginationRange(10, 10)).toEqual([1, 'ellipsis-left', 6, 7, 8, 9, 10])
    expect(getPaginationRange(8, 10)).toEqual([1, 'ellipsis-left', 6, 7, 8, 9, 10])
  })

  it('truncates both sides in the middle, current page centred', () => {
    expect(getPaginationRange(5, 10)).toEqual([1, 'ellipsis-left', 4, 5, 6, 'ellipsis-right', 10])
    expect(getPaginationRange(50, 100)).toEqual([
      1,
      'ellipsis-left',
      49,
      50,
      51,
      'ellipsis-right',
      100,
    ])
  })

  it('always keeps the first and last page visible', () => {
    const slots = getPaginationRange(50, 100)
    expect(slots[0]).toBe(1)
    expect(slots[slots.length - 1]).toBe(100)
  })

  it('never places an ellipsis at the first or last slot', () => {
    for (let page = 1; page <= 20; page++) {
      const slots = getPaginationRange(page, 20)
      expect(typeof slots[0]).toBe('number')
      expect(typeof slots[slots.length - 1]).toBe('number')
    }
  })

  it('widens the window with a larger siblingCount', () => {
    expect(getPaginationRange(5, 20, 2)).toEqual([
      1,
      'ellipsis-left',
      3,
      4,
      5,
      6,
      7,
      'ellipsis-right',
      20,
    ])
  })
})
