import { flexRender, type Table } from '@tanstack/react-table'
import type { AdminUser } from '../-users-types'

export function UsersTable({ table }: { table: Table<AdminUser> }) {
  return (
    <table data-help-id="admin-users-table" className="w-full text-sm border-t border-b border-gray-200">
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
        {table.getRowModel().rows.map((row) => (
          <tr
            key={row.id}
            className="border-b border-gray-200 last:border-b-0"
          >
            {row.getVisibleCells().map((cell) => (
              <td
                key={cell.id}
                className="py-4 pr-4 align-top text-gray-900"
              >
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
