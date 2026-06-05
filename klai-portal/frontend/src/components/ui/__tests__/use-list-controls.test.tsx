import { describe, it, expect } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useListControls } from '../use-list-controls'

const items = (n: number) => Array.from({ length: n }, (_, i) => `item-${i + 1}`)
const contains = (item: string, q: string) => item.includes(q.trim().toLowerCase())

describe('useListControls', () => {
  it('shows all items and no controls at or below the page size', () => {
    const { result } = renderHook(() =>
      useListControls(items(10), { filter: contains }),
    )
    expect(result.current.pageItems).toHaveLength(10)
    expect(result.current.showSearch).toBe(false)
    expect(result.current.showPagination).toBe(false)
    expect(result.current.pageCount).toBe(1)
  })

  it('reveals search and pagination once past the page size', () => {
    const { result } = renderHook(() =>
      useListControls(items(11), { filter: contains }),
    )
    expect(result.current.pageItems).toHaveLength(10)
    expect(result.current.showSearch).toBe(true)
    expect(result.current.showPagination).toBe(true)
    expect(result.current.pageCount).toBe(2)
  })

  it('never shows search without a filter, even on a long list', () => {
    const { result } = renderHook(() => useListControls(items(25)))
    expect(result.current.showSearch).toBe(false)
    expect(result.current.showPagination).toBe(true)
  })

  it('paginates the filtered set and exposes the right page slice', () => {
    const { result } = renderHook(() =>
      useListControls(items(25), { pageSize: 10, filter: contains }),
    )
    expect(result.current.pageItems[0]).toBe('item-1')

    act(() => result.current.setPage(3))
    expect(result.current.page).toBe(3)
    expect(result.current.pageItems).toEqual(['item-21', 'item-22', 'item-23', 'item-24', 'item-25'])
  })

  it('clamps an out-of-range page instead of stranding on an empty page', () => {
    const { result } = renderHook(() =>
      useListControls(items(25), { pageSize: 10, filter: contains }),
    )
    act(() => result.current.setPage(99))
    expect(result.current.page).toBe(3)
    expect(result.current.pageItems).toHaveLength(5)
  })

  it('filters by query, resets to page 1, and hides the pager when narrowed to one page', () => {
    const { result } = renderHook(() =>
      useListControls(items(25), { pageSize: 10, filter: contains }),
    )
    act(() => result.current.setPage(3))
    // "item-1" matches item-1 and item-10..item-19 → 11 results → still paged.
    act(() => result.current.setQuery('item-1'))
    expect(result.current.page).toBe(1)
    expect(result.current.filteredCount).toBe(11)
    expect(result.current.showPagination).toBe(true)
    expect(result.current.showSearch).toBe(true)

    // "item-25" matches one row → pager hidden, search stays available.
    act(() => result.current.setQuery('item-25'))
    expect(result.current.filteredCount).toBe(1)
    expect(result.current.showPagination).toBe(false)
    expect(result.current.showSearch).toBe(true)
    expect(result.current.totalCount).toBe(25)
  })
})
