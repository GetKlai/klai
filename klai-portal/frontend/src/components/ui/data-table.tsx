import * as React from 'react'
import { cn } from '@/lib/utils'

function DataTable({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <table
      className={cn('w-full border-y border-gray-200 text-sm', className)}
      {...props}
    />
  )
}

function DataTableHeader({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={className} {...props} />
}

function DataTableBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={className} {...props} />
}

interface DataTableRowProps extends React.HTMLAttributes<HTMLTableRowElement> {
  interactive?: boolean
  /**
   * Tint the row while an inline delete-confirm overlay is open, so the
   * `bg-[var(--color-hover)]` overlay has no seam against the row. Mirrors
   * `ListRow confirming`.
   */
  confirming?: boolean
}

function DataTableRow({
  interactive = false,
  confirming = false,
  className,
  ...props
}: DataTableRowProps) {
  return (
    <tr
      className={cn(
        'border-b border-gray-200 last:border-b-0 transition-colors',
        interactive && 'cursor-pointer klai-hover',
        confirming && 'bg-[var(--color-hover)]',
        className,
      )}
      {...props}
    />
  )
}

interface DataTableHeadProps extends React.ThHTMLAttributes<HTMLTableCellElement> {
  align?: 'left' | 'right'
}

function DataTableHead({ align = 'left', className, ...props }: DataTableHeadProps) {
  return (
    <th
      className={cn(
        'px-4 py-3 text-left text-xs font-medium text-gray-400',
        align === 'right' && 'text-right',
        className,
      )}
      {...props}
    />
  )
}

interface DataTableCellProps extends React.TdHTMLAttributes<HTMLTableCellElement> {
  align?: 'left' | 'right'
}

function DataTableCell({ align = 'left', className, ...props }: DataTableCellProps) {
  return (
    <td
      className={cn(
        'px-4 py-4 align-middle text-gray-900',
        align === 'right' && 'text-right',
        className,
      )}
      {...props}
    />
  )
}

export {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
}
