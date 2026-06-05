import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import * as m from '@/paraglide/messages'

export interface PaginationProps {
  /** Current page, 1-based. */
  page: number
  /** Total number of pages (>= 1). */
  pageCount: number
  /** Called with the new 1-based page when the user navigates. */
  onPageChange: (page: number) => void
  /**
   * How many page numbers to show on each side of the current page before
   * collapsing to an ellipsis. Default 1 → at most 7 slots
   * (first, …, p-1, p, p+1, …, last).
   */
  siblingCount?: number
  /** Accessible label for the previous-page control. Defaults to the Paraglide string. */
  previousLabel?: string
  /** Accessible label for the next-page control. Defaults to the Paraglide string. */
  nextLabel?: string
  /** Accessible label for a numbered page button. Defaults to the Paraglide string. */
  pageLabel?: (page: number) => string
  className?: string
}

type PageSlot = number | 'ellipsis-left' | 'ellipsis-right'

const range = (start: number, end: number): number[] =>
  Array.from({ length: Math.max(0, end - start + 1) }, (_, i) => start + i)

/**
 * The canonical numbered-pagination range: always shows the first and last
 * page, a window of `siblingCount` pages around the current page, and an
 * ellipsis where pages are skipped. The ellipsis never sits at the very start
 * or end of the sequence (W3C / USWDS / Carbon / MUI convention).
 *
 * Exported for unit testing.
 */
export function getPaginationRange(
  page: number,
  pageCount: number,
  siblingCount = 1,
): PageSlot[] {
  // first + last + current + 2 ellipsis + (siblingCount on each side).
  const totalSlots = siblingCount * 2 + 5
  if (pageCount <= totalSlots) return range(1, pageCount)

  const leftSibling = Math.max(page - siblingCount, 1)
  const rightSibling = Math.min(page + siblingCount, pageCount)

  // Show an ellipsis only when it hides more than one page (never adjacent to
  // the boundary it replaces).
  const showLeftEllipsis = leftSibling > 2
  const showRightEllipsis = rightSibling < pageCount - 1

  const firstPage = 1
  const lastPage = pageCount

  if (!showLeftEllipsis && showRightEllipsis) {
    const leftCount = siblingCount * 2 + 3
    return [...range(1, leftCount), 'ellipsis-right', lastPage]
  }

  if (showLeftEllipsis && !showRightEllipsis) {
    const rightCount = siblingCount * 2 + 3
    return [firstPage, 'ellipsis-left', ...range(pageCount - rightCount + 1, pageCount)]
  }

  return [
    firstPage,
    'ellipsis-left',
    ...range(leftSibling, rightSibling),
    'ellipsis-right',
    lastPage,
  ]
}

const arrowClass =
  'inline-flex h-8 w-8 items-center justify-center rounded-full text-gray-600 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] disabled:pointer-events-none disabled:opacity-40'

/**
 * Numbered pager for list and table overviews: previous / page numbers (with
 * ellipsis truncation) / next. The current page is highlighted and not
 * clickable.
 *
 * Presentational and controlled — it owns no page state. Pair it with
 * `useListControls`, which encodes the "only paginate past the page size"
 * rule so the control is hidden entirely on short lists.
 */
function Pagination({
  page,
  pageCount,
  onPageChange,
  siblingCount = 1,
  previousLabel = m.pagination_previous(),
  nextLabel = m.pagination_next(),
  pageLabel = (n) => m.pagination_page({ n: String(n) }),
  className,
}: PaginationProps) {
  const slots = getPaginationRange(page, pageCount, siblingCount)
  const atStart = page <= 1
  const atEnd = page >= pageCount

  return (
    <nav
      aria-label="Paginering"
      className={cn('flex items-center justify-center gap-1', className)}
    >
      <button
        type="button"
        aria-label={previousLabel}
        disabled={atStart}
        onClick={() => onPageChange(Math.max(1, page - 1))}
        className={arrowClass}
      >
        <ChevronLeft className="h-4 w-4" />
      </button>

      {slots.map((slot) =>
        typeof slot === 'number' ? (
          slot === page ? (
            <span
              key={slot}
              aria-current="page"
              className="inline-flex h-8 min-w-8 items-center justify-center rounded-full bg-gray-900 px-2 text-sm font-medium text-white"
            >
              {slot}
            </span>
          ) : (
            <button
              key={slot}
              type="button"
              aria-label={pageLabel(slot)}
              onClick={() => onPageChange(slot)}
              className="inline-flex h-8 min-w-8 items-center justify-center rounded-full px-2 text-sm text-gray-600 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
            >
              {slot}
            </button>
          )
        ) : (
          <span
            key={slot}
            aria-hidden="true"
            className="inline-flex h-8 w-8 items-center justify-center text-sm text-gray-400"
          >
            …
          </span>
        ),
      )}

      <button
        type="button"
        aria-label={nextLabel}
        disabled={atEnd}
        onClick={() => onPageChange(Math.min(pageCount, page + 1))}
        className={arrowClass}
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </nav>
  )
}

export { Pagination }
