import { flexRender, type Table } from '@tanstack/react-table'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import type { AdminUser } from '../-users-types'

interface Props {
  table: Table<AdminUser>
  onRowClick?: (row: AdminUser) => void
}

export function UsersTable({ table, onRowClick }: Props) {
  return (
    <DataTable data-help-id="admin-users-table">
      <DataTableHeader>
        {table.getHeaderGroups().map((headerGroup) => (
          <DataTableRow key={headerGroup.id}>
            {headerGroup.headers.map((header) => {
              const isActionHeader = header.column.id === 'actions'
              return (
                <DataTableHead
                  key={header.id}
                  align={isActionHeader ? 'right' : 'left'}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </DataTableHead>
              )
            })}
          </DataTableRow>
        ))}
      </DataTableHeader>
      <DataTableBody>
        {table.getRowModel().rows.map((row) => {
          const clickable = !!onRowClick
          return (
            <DataTableRow
              key={row.id}
              interactive={clickable}
              onClick={
                clickable ? () => onRowClick(row.original) : undefined
              }
            >
              {row.getVisibleCells().map((cell) => {
                const isActionCell = cell.column.id === 'actions'
                const isDateCell = cell.column.id === 'created_at'
                return (
                  <DataTableCell
                    key={cell.id}
                    align={isActionCell ? 'right' : 'left'}
                    className={isDateCell ? 'whitespace-nowrap' : undefined}
                    onClick={
                      clickable && isActionCell
                        ? (e) => e.stopPropagation()
                        : undefined
                    }
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </DataTableCell>
                )
              })}
            </DataTableRow>
          )
        })}
      </DataTableBody>
    </DataTable>
  )
}
