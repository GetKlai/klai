import { flexRender, type Table } from '@tanstack/react-table'
import type { AdminUser } from '../-users-types'

interface Props {
  table: Table<AdminUser>
  onRowClick?: (row: AdminUser) => void
}

export function UsersTable({ table, onRowClick }: Props) {
  return (
    <table
      data-help-id="admin-users-table"
      className="w-full text-sm border-t border-b border-gray-200"
    >
      <thead>
        {table.getHeaderGroups().map((headerGroup) => (
          <tr key={headerGroup.id} className="border-b border-gray-200">
            {headerGroup.headers.map((header) => (
              <th
                key={header.id}
                className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide"
              >
                {flexRender(header.column.columnDef.header, header.getContext())}
              </th>
            ))}
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
                    className="py-4 pr-4 align-top text-gray-900"
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
