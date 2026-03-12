import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select } from '@/components/ui/select'
import { Tooltip } from '@/components/ui/tooltip'
import { Trash2, Send, Loader2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime, plural } from '@/paraglide/registry'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/users/')({
  component: UsersPage,
})

type Role = 'admin' | 'member'

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: Role
  preferred_language: 'nl' | 'en'
  created_at: string
  invite_pending: boolean
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function RoleBadge({ role }: { role: Role }) {
  return role === 'admin'
    ? <Badge variant="accent" size="sm">{m.admin_users_role_admin()}</Badge>
    : <Badge variant="secondary" size="sm">{m.admin_users_role_member()}</Badge>
}

const columnHelper = createColumnHelper<User>()

function UsersPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const currentUserId = auth.user?.profile?.sub
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-users', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/users`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_users_error_fetch({ status: String(res.status) }))
      return res.json() as Promise<{ users: User[] }>
    },
    enabled: !!token,
  })

  const users = data?.users ?? []

  const resendInviteMutation = useMutation({
    mutationFn: async (user: User) => {
      const res = await fetch(`${API_BASE}/api/admin/users/${user.zitadel_user_id}/resend-invite`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_users_error_resend_invite({ status: String(res.status) }))
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (user: User) => {
      const res = await fetch(`${API_BASE}/api/admin/users/${user.zitadel_user_id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_users_error_delete({ status: String(res.status) }))
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const roleMutation = useMutation({
    mutationFn: async ({ user, newRole }: { user: User; newRole: Role }) => {
      const res = await fetch(`${API_BASE}/api/admin/users/${user.zitadel_user_id}/role`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ role: newRole }),
      })
      if (!res.ok) throw new Error(m.admin_users_error_role({ status: String(res.status) }))
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  function handleDelete(user: User) {
    const name = `${user.first_name} ${user.last_name}`
    if (!window.confirm(m.admin_users_confirm_delete({ name }))) return
    deleteMutation.mutate(user)
  }

  const pageError =
    (error instanceof Error ? error.message : error ? m.admin_users_error_generic() : null) ??
    (deleteMutation.error instanceof Error ? deleteMutation.error.message : deleteMutation.error ? m.admin_users_error_delete_generic() : null) ??
    (roleMutation.error instanceof Error ? roleMutation.error.message : roleMutation.error ? m.admin_users_error_role_generic() : null) ??
    (resendInviteMutation.error instanceof Error ? resendInviteMutation.error.message : resendInviteMutation.error ? m.admin_users_error_resend_invite_generic() : null)

  const columns = [
    columnHelper.accessor((row) => `${row.first_name} ${row.last_name}`, {
      id: 'naam',
      header: () => m.admin_users_col_name(),
      cell: (info) => info.getValue(),
    }),
    columnHelper.accessor('email', {
      header: () => m.admin_users_col_email(),
      cell: (info) => info.getValue(),
    }),
    columnHelper.accessor('role', {
      header: () => m.admin_users_col_role(),
      cell: (info) => (
        <div className="flex flex-col gap-1">
          <RoleBadge role={info.getValue()} />
          {info.row.original.invite_pending && (
            <Badge variant="outline" className="text-amber-600 border-amber-300 bg-amber-50 text-xs">
              {m.admin_users_invite_pending()}
            </Badge>
          )}
        </div>
      ),
    }),
    columnHelper.accessor('created_at', {
      header: () => m.admin_users_col_invited(),
      cell: (info) => formatDate(info.getValue()),
    }),
    columnHelper.display({
      id: 'acties',
      header: () => m.admin_users_col_actions(),
      cell: ({ row }) => {
        const user = row.original
        const isSelf = user.zitadel_user_id === currentUserId
        const isChangingRole =
          roleMutation.isPending &&
          roleMutation.variables?.user.zitadel_user_id === user.zitadel_user_id
        const isResending =
          resendInviteMutation.isPending &&
          resendInviteMutation.variables?.zitadel_user_id === user.zitadel_user_id
        return (
          <div className="flex items-center gap-2">
            <Select
              value={user.role}
              disabled={isSelf || isChangingRole}
              onChange={(e) => roleMutation.mutate({ user, newRole: e.target.value as Role })}
              className="w-auto px-2 py-1 text-xs"
            >
              <option value="admin">{m.admin_users_role_admin()}</option>
              <option value="member">{m.admin_users_role_member()}</option>
            </Select>
            {user.invite_pending && (
              <Tooltip label={m.admin_users_resend_invite()}>
                <button
                  disabled={isResending}
                  onClick={() => resendInviteMutation.mutate(user)}
                  aria-label={m.admin_users_resend_invite()}
                  className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 disabled:opacity-40"
                >
                  {isResending
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Send className="h-3.5 w-3.5" />
                  }
                </button>
              </Tooltip>
            )}
            <Tooltip label={m.admin_users_delete()}>
              <button
                disabled={isSelf}
                onClick={() => handleDelete(user)}
                aria-label={m.admin_users_delete()}
                className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70 disabled:opacity-40"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </Tooltip>
          </div>
        )
      },
    }),
  ]

  const table = useReactTable({
    data: users,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="p-8 space-y-6 max-w-4xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.admin_users_heading()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && !error && (
              plural(getLocale(), users.length) === 'one'
                ? m.admin_users_count_one()
                : m.admin_users_count_other({ count: String(users.length) })
            )}
          </p>
        </div>
        <Button onClick={() => navigate({ to: '/admin/users/invite' })}>
          {m.admin_users_invite_button()}
        </Button>
      </div>

      {pageError && (
        <p className="text-sm text-[var(--color-destructive)]">{pageError}</p>
      )}

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              {m.admin_users_loading()}
            </p>
          ) : users.length === 0 ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              {m.admin_users_empty()}
            </p>
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
