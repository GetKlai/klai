import { flexRender, type Table } from '@tanstack/react-table'
import { cn } from '@/lib/utils'
import type { AdminUser } from '../-users-types'

interface Props {
  table: Table<AdminUser>
  onRowClick?: (row: AdminUser) => void
}

export function UsersTable({ table, onRowClick }: Props) {
  return (
    <table
      data-help-id="admin-users-table"
      className="w-full border-y border-gray-200 text-sm"
    >
      <thead>
        {table.getHeaderGroups().map((headerGroup) => (
          <tr key={headerGroup.id} className="border-b border-gray-200">
            {headerGroup.headers.map((header) => {
              const isActionHeader = header.column.id === 'actions'
              return (
                <th
                  key={header.id}
                  className={cn(
                    'px-3 py-3 text-left text-xs font-medium text-gray-400',
                    isActionHeader && 'text-right',
                  )}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              )
            })}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => {
          const clickable = !!onRowClick
          return (
            <tr
              key={row.id}
              onClick={
                clickable ? () => onRowClick(row.original) : undefined
              }
              className={
                clickable
                  ? 'border-b border-gray-200 last:border-b-0 cursor-pointer klai-hover'
                  : 'border-b border-gray-200 last:border-b-0'
              }
            >
              {row.getVisibleCells().map((cell) => {
                const isActionCell = cell.column.id === 'actions'
                return (
                  <td
                    key={cell.id}
                    className={cn(
                      'px-3 py-4 align-middle text-gray-900',
                      isActionCell && 'text-right',
                    )}
                    onClick={
                      clickable && isActionCell
                        ? (e) => e.stopPropagation()
                        : undefined
                    }
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                )
              })}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
