import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Loader2, Eye } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/groups/')({
  component: AdminGroups,
})

interface Group {
  id: number
  name: string
  products: string[]
}

const columnHelper = createColumnHelper<Group>()

function AdminGroups() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()

  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch groups (${res.status})`)
      return res.json() as Promise<{ groups: Group[] }>
    },
    enabled: !!token,
  })

  const groups = data?.groups ?? []

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_groups_name(),
      cell: (info) => (
        <span className="font-medium text-[var(--color-purple-deep)]">{info.getValue()}</span>
      ),
    }),
    columnHelper.accessor('products', {
      header: () => 'Product',
      cell: (info) => (
        <div className="flex gap-1">
          {info.getValue().map((p) => (
            <Badge key={p} variant="secondary" className="text-xs capitalize">
              {p}
            </Badge>
          ))}
        </div>
      ),
    }),
    columnHelper.display({
      id: 'actions',
      header: () => '',
      cell: ({ row }) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/groups/$groupId', params: { groupId: String(row.original.id) } })}
          aria-label={row.original.name}
        >
          <Eye className="h-4 w-4" />
        </Button>
      ),
    }),
  ]

  const table = useReactTable({
    data: groups,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="p-8 space-y-6">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_groups_title()}
        </h1>
      </div>

      {error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {error instanceof Error ? error.message : String(error)}
        </p>
      )}

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Loading...
            </p>
          ) : groups.length === 0 ? (
            <div className="px-6 py-12 text-center space-y-3">
              <p className="text-sm font-medium text-[var(--color-purple-deep)]">
                {m.admin_groups_empty()}
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                {m.admin_groups_empty_description()}
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id} className="border-b border-[var(--color-border)]">
                    {headerGroup.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide"
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row, i) => (
                  <tr
                    key={row.id}
                    className={
                      i % 2 === 0
                        ? 'bg-[var(--color-card)]'
                        : 'bg-[var(--color-secondary)]'
                    }
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        className="px-6 py-3 text-[var(--color-purple-deep)]"
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
