import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Loader2, Eye, Trash2, Plus } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/knowledge-bases/')({
  component: AdminKnowledgeBases,
})

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  created_at: string
  created_by: string
}

const columnHelper = createColumnHelper<KnowledgeBase>()

function AdminKnowledgeBases() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newSlug, setNewSlug] = useState('')
  const [newDescription, setNewDescription] = useState('')

  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-knowledge-bases', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-bases`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch knowledge bases (${res.status})`)
      return res.json() as Promise<{ knowledge_bases: KnowledgeBase[] }>
    },
    enabled: !!token,
  })

  const knowledgeBases = data?.knowledge_bases ?? []

  const createMutation = useMutation({
    mutationFn: async (payload: { name: string; slug: string; description: string }) => {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-bases`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      })
      if (!res.ok) throw new Error(`Failed to create knowledge base (${res.status})`)
      return res.json()
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-knowledge-bases', token] })
      setShowCreate(false)
      setNewName('')
      setNewSlug('')
      setNewDescription('')
      toast.success(m.admin_kb_success_created())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-bases/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to delete knowledge base (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-knowledge-bases', token] })
      toast.success(m.admin_kb_success_deleted())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const columns = [
    columnHelper.accessor('name', {
      header: () => m.admin_kb_col_name(),
      cell: (info) => (
        <span className="font-medium text-[var(--color-purple-deep)]">
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.accessor('slug', {
      header: () => m.admin_kb_col_slug(),
      cell: (info) => (
        <Badge variant="secondary" className="font-mono text-xs">
          {info.getValue()}
        </Badge>
      ),
    }),
    columnHelper.display({
      id: 'actions',
      header: () => m.admin_kb_col_actions(),
      cell: ({ row }) => {
        const isDeleting =
          deleteMutation.isPending && deleteMutation.variables === row.original.id
        return (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                navigate({
                  to: '/admin/knowledge-bases/$kbId',
                  params: { kbId: String(row.original.id) },
                })
              }
              aria-label={row.original.name}
            >
              <Eye className="h-4 w-4" />
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="ghost" size="sm" disabled={isDeleting}>
                  {isDeleting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
                  )}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{m.admin_kb_delete_title()}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {m.admin_kb_delete_description()}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{m.admin_kb_delete_cancel()}</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => deleteMutation.mutate(row.original.id)}
                  >
                    {m.admin_kb_delete_confirm()}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        )
      },
    }),
  ]

  const table = useReactTable({
    data: knowledgeBases,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.admin_kb_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.admin_kb_subtitle()}
          </p>
        </div>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4 mr-2" />
          {m.admin_kb_add()}
        </Button>
      </div>

      {showCreate && (
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="kb-name">{m.admin_kb_field_name()}</Label>
                  <Input
                    id="kb-name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="kb-slug">{m.admin_kb_field_slug()}</Label>
                  <Input
                    id="kb-slug"
                    value={newSlug}
                    onChange={(e) => setNewSlug(e.target.value)}
                    className="font-mono"
                  />
                </div>
              </div>
              <div className="space-y-1">
                <Label htmlFor="kb-desc">{m.admin_kb_field_description()}</Label>
                <Input
                  id="kb-desc"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  onClick={() =>
                    createMutation.mutate({
                      name: newName,
                      slug: newSlug,
                      description: newDescription,
                    })
                  }
                  disabled={
                    !newName.trim() || !newSlug.trim() || createMutation.isPending
                  }
                >
                  {createMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    m.admin_kb_create_submit()
                  )}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setShowCreate(false)
                    setNewName('')
                    setNewSlug('')
                    setNewDescription('')
                  }}
                >
                  {m.admin_kb_delete_cancel()}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

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
              {m.admin_kb_loading()}
            </p>
          ) : knowledgeBases.length === 0 ? (
            <div className="px-6 py-12 text-center space-y-3">
              <p className="text-sm font-medium text-[var(--color-purple-deep)]">
                {m.admin_kb_empty()}
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                {m.admin_kb_empty_description()}
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr
                    key={headerGroup.id}
                    className="border-b border-[var(--color-border)]"
                  >
                    {headerGroup.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide"
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
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
