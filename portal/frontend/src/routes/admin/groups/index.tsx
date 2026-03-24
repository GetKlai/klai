import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog'
import { Loader2, Eye } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/groups/')({
  component: AdminGroups,
})

interface Group {
  id: number
  name: string
  description: string | null
  created_at: string
  created_by: string
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

const columnHelper = createColumnHelper<Group>()

function AdminGroups() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [duplicateError, setDuplicateError] = useState(false)

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

  const createMutation = useMutation({
    mutationFn: async (body: { name: string; description: string }) => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      })
      if (res.status === 409) {
        throw new Error('duplicate')
      }
      if (!res.ok) throw new Error(`Failed to create group (${res.status})`)
      return res.json() as Promise<Group>
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      toast.success(m.admin_groups_success_created())
      closeDialog()
    },
    onError: (err: Error) => {
      if (err.message === 'duplicate') {
        setDuplicateError(true)
      } else {
        toast.error(err.message)
      }
    },
  })

  function closeDialog() {
    setDialogOpen(false)
    setName('')
    setDescription('')
    setDuplicateError(false)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setDuplicateError(false)
    createMutation.mutate({ name: name.trim(), description: description.trim() })
  }

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_groups_name(),
      cell: (info) => info.getValue(),
    }),
    columnHelper.accessor('description', {
      header: () => m.admin_groups_description(),
      cell: (info) => (
        <span className="text-[var(--color-muted-foreground)]">
          {info.getValue() || '-'}
        </span>
      ),
    }),
    columnHelper.accessor('created_at', {
      header: () => m.admin_groups_created_at(),
      cell: (info) => formatDate(info.getValue()),
    }),
    columnHelper.accessor('created_by', {
      header: () => m.admin_groups_created_by(),
      cell: (info) => info.getValue(),
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
    <div className="p-8 space-y-6 max-w-4xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.admin_groups_title()}
          </h1>
        </div>
        <Button onClick={() => setDialogOpen(true)}>
          {m.admin_groups_create()}
        </Button>
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
              <Button onClick={() => setDialogOpen(true)} variant="outline">
                {m.admin_groups_create()}
              </Button>
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

      <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) closeDialog() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{m.admin_groups_create()}</DialogTitle>
            <DialogDescription className="sr-only">
              {m.admin_groups_create()}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="group-name">{m.admin_groups_name()}</Label>
              <Input
                id="group-name"
                value={name}
                onChange={(e) => { setName(e.target.value); setDuplicateError(false) }}
                placeholder={m.admin_groups_name_placeholder()}
                required
                autoFocus
              />
              {duplicateError && (
                <p className="text-sm text-[var(--color-destructive)]">
                  {m.admin_groups_error_duplicate()}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="group-description">{m.admin_groups_description()}</Label>
              <textarea
                id="group-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={m.admin_groups_description_placeholder()}
                rows={3}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-purple-deep)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={closeDialog}
                disabled={createMutation.isPending}
              >
                {m.admin_users_cancel()}
              </Button>
              <Button type="submit" disabled={createMutation.isPending || !name.trim()}>
                {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {m.admin_groups_create()}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
