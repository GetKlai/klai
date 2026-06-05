import { useMemo, useState } from 'react'

export interface UseListControlsOptions<T> {
  /** Items per page and the search/pagination visibility threshold. Default 10. */
  pageSize?: number
  /**
   * Row matcher for the search box. When omitted, the search control is never
   * shown (the list has nothing to filter on). The raw query is passed as
   * typed; lower-casing/trimming inside the matcher is the caller's job.
   */
  filter?: (item: T, query: string) => boolean
  /**
   * Controlled query value. Provide together with `onQueryChange` to drive the
   * search term from outside (e.g. URL search state on the transcribe page).
   * When omitted, the hook owns the query in internal state.
   */
  query?: string
  /** Controlled query setter. Required when `query` is provided. */
  onQueryChange?: (query: string) => void
}

export interface ListControls<T> {
  /** Raw search query as typed. */
  query: string
  /** Sets the query and resets to the first page. */
  setQuery: (query: string) => void
  /** Current page, 1-based and clamped to a valid range. */
  page: number
  /** Sets the current page (clamped on read). */
  setPage: (page: number) => void
  /** Total number of pages for the filtered set (>= 1). */
  pageCount: number
  /** The items to render for the current page. */
  pageItems: T[]
  /** Number of items after filtering. */
  filteredCount: number
  /** Number of items before filtering. */
  totalCount: number
  /**
   * Whether to render a search box: a `filter` is configured AND the
   * unfiltered list is longer than one page. Short lists get no search.
   */
  showSearch: boolean
  /**
   * Whether to render the pager: the filtered list is longer than one page.
   * A search that narrows the set to one page hides the pager but keeps the
   * search box visible (so the user can clear it).
   */
  showPagination: boolean
}

/**
 * The canonical list/table overview controller. Encodes the Klai rule that
 * search and pagination only appear once a collection grows past the page
 * size (default 10), so short lists stay free of pager/filter chrome.
 *
 * Pure and presentation-agnostic: pair the returned values with `SearchInput`
 * and `Pagination`. It owns query + page state and derives everything else.
 */
export function useListControls<T>(
  items: T[],
  options: UseListControlsOptions<T> = {},
): ListControls<T> {
  const { pageSize = 10, filter, query: controlledQuery, onQueryChange } = options
  const isControlled = controlledQuery !== undefined
  const [internalQuery, setInternalQuery] = useState('')
  const query = isControlled ? controlledQuery : internalQuery
  const [page, setPageState] = useState(1)

  const filtered = useMemo(() => {
    if (!filter || !query.trim()) return items
    return items.filter((item) => filter(item, query))
  }, [items, filter, query])

  const filteredCount = filtered.length
  const pageCount = Math.max(1, Math.ceil(filteredCount / pageSize))
  // Clamp on read so a shrinking result set (e.g. after typing a query) never
  // strands the user on an out-of-range, empty page.
  const safePage = Math.min(Math.max(1, page), pageCount)

  const pageItems = useMemo(
    () => filtered.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filtered, safePage, pageSize],
  )

  return {
    query,
    setQuery: (next) => {
      if (isControlled) onQueryChange?.(next)
      else setInternalQuery(next)
      setPageState(1)
    },
    page: safePage,
    setPage: setPageState,
    pageCount,
    pageItems,
    filteredCount,
    totalCount: items.length,
    showSearch: !!filter && items.length > pageSize,
    showPagination: filteredCount > pageSize,
  }
}
