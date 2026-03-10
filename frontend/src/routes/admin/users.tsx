import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
} from '@tanstack/react-table'
import { useState, useEffect } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/users')({
  component: UsersPage,
})

type Role = 'admin' | 'member'

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: Role
  created_at: string
}

interface InviteForm {
  first_name: string
  last_name: string
  email: string
  role: Role
}

function formatDutchDate(isoString: string): string {
  const date = new Date(isoString)
  return date.toLocaleDateString('nl-NL', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function RoleBadge({ role }: { role: Role }) {
  if (role === 'admin') {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-purple-100 text-purple-700">
        {m.admin_users_role_admin()}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-600">
      {m.admin_users_role_member()}
    </span>
  )
}

const columnHelper = createColumnHelper<User>()

function UsersPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const currentUserId = auth.user?.profile?.sub

  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [showInviteForm, setShowInviteForm] = useState(false)
  const [inviteForm, setInviteForm] = useState<InviteForm>({
    first_name: '',
    last_name: '',
    email: '',
    role: 'member',
  })
  const [inviteLoading, setInviteLoading] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)

  const [roleChanging, setRoleChanging] = useState<string | null>(null)

  useEffect(() => {
    async function fetchUsers() {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/api/admin/users`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(m.admin_users_error_fetch({ status: String(res.status) }))
        const data = await res.json()
        setUsers(data.users)
      } catch (err) {
        setError(err instanceof Error ? err.message : m.admin_users_error_generic())
      } finally {
        setLoading(false)
      }
    }

    if (token) {
      fetchUsers()
    }
  }, [token])

  async function handleDelete(user: User) {
    const name = `${user.first_name} ${user.last_name}`
    if (!window.confirm(m.admin_users_confirm_delete({ name }))) return

    setUsers((prev) => prev.filter((u) => u.zitadel_user_id !== user.zitadel_user_id))

    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_BASE_URL}/api/admin/users/${user.zitadel_user_id}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      )
      if (!res.ok) throw new Error(m.admin_users_error_delete({ status: String(res.status) }))
    } catch (err) {
      setError(err instanceof Error ? err.message : m.admin_users_error_delete_generic())
      setUsers((prev) => [...prev, user])
    }
  }

  async function handleRoleChange(user: User, newRole: Role) {
    setRoleChanging(user.zitadel_user_id)
    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_BASE_URL}/api/admin/users/${user.zitadel_user_id}/role`,
        {
          method: 'PATCH',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ role: newRole }),
        }
      )
      if (!res.ok) throw new Error(m.admin_users_error_role({ status: String(res.status) }))
      setUsers((prev) =>
        prev.map((u) =>
          u.zitadel_user_id === user.zitadel_user_id ? { ...u, role: newRole } : u
        )
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : m.admin_users_error_role_generic())
    } finally {
      setRoleChanging(null)
    }
  }

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault()
    setInviteLoading(true)
    setInviteError(null)

    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/api/admin/users/invite`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(inviteForm),
      })
      if (!res.ok) throw new Error(m.admin_users_error_invite({ status: String(res.status) }))

      setInviteForm({ first_name: '', last_name: '', email: '', role: 'member' })
      setShowInviteForm(false)

      const refreshRes = await fetch(`${import.meta.env.VITE_API_BASE_URL}/api/admin/users`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (refreshRes.ok) {
        const data = await refreshRes.json()
        setUsers(data.users)
      }
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : m.admin_users_error_invite_generic())
    } finally {
      setInviteLoading(false)
    }
  }

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
      cell: (info) => <RoleBadge role={info.getValue()} />,
    }),
    columnHelper.accessor('created_at', {
      header: () => m.admin_users_col_since(),
      cell: (info) => formatDutchDate(info.getValue()),
    }),
    columnHelper.display({
      id: 'acties',
      header: () => m.admin_users_col_actions(),
      cell: ({ row }) => {
        const user = row.original
        const isSelf = user.zitadel_user_id === currentUserId
        const isChangingRole = roleChanging === user.zitadel_user_id
        return (
          <div className="flex items-center gap-2">
            <select
              value={user.role}
              disabled={isSelf || isChangingRole}
              onChange={(e) => handleRoleChange(user, e.target.value as Role)}
              className="rounded border border-[var(--color-border)] bg-transparent px-2 py-1 text-xs text-[var(--color-purple-deep)] outline-none focus:ring-2 focus:ring-[var(--color-ring)] disabled:opacity-50"
            >
              <option value="admin">{m.admin_users_role_admin()}</option>
              <option value="member">{m.admin_users_role_member()}</option>
            </select>
            <Button
              variant="destructive"
              size="sm"
              disabled={isSelf}
              onClick={() => handleDelete(user)}
            >
              {m.admin_users_delete()}
            </Button>
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
            {m.admin_users_subtitle()}
          </p>
        </div>
        <Button
          onClick={() => {
            setShowInviteForm((prev) => !prev)
            setInviteError(null)
          }}
        >
          {m.admin_users_invite_button()}
        </Button>
      </div>

      {showInviteForm && (
        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleInvite} className="space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1">
                  <label className="text-sm font-medium text-[var(--color-purple-deep)]">
                    {m.admin_users_field_first_name()}
                  </label>
                  <input
                    type="text"
                    required
                    value={inviteForm.first_name}
                    onChange={(e) =>
                      setInviteForm((prev) => ({ ...prev, first_name: e.target.value }))
                    }
                    className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-sm font-medium text-[var(--color-purple-deep)]">
                    {m.admin_users_field_last_name()}
                  </label>
                  <input
                    type="text"
                    required
                    value={inviteForm.last_name}
                    onChange={(e) =>
                      setInviteForm((prev) => ({ ...prev, last_name: e.target.value }))
                    }
                    className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1">
                  <label className="text-sm font-medium text-[var(--color-purple-deep)]">
                    {m.admin_users_field_email()}
                  </label>
                  <input
                    type="email"
                    required
                    value={inviteForm.email}
                    onChange={(e) =>
                      setInviteForm((prev) => ({ ...prev, email: e.target.value }))
                    }
                    className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-sm font-medium text-[var(--color-purple-deep)]">
                    {m.admin_users_field_role()}
                  </label>
                  <select
                    value={inviteForm.role}
                    onChange={(e) =>
                      setInviteForm((prev) => ({ ...prev, role: e.target.value as Role }))
                    }
                    className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
                  >
                    <option value="member">{m.admin_users_role_member()}</option>
                    <option value="admin">{m.admin_users_role_admin()}</option>
                  </select>
                </div>
              </div>
              {inviteError && (
                <p className="text-sm text-[var(--color-destructive)]">{inviteError}</p>
              )}
              <div className="flex gap-2">
                <Button type="submit" disabled={inviteLoading}>
                  {inviteLoading ? m.admin_users_invite_submit_loading() : m.admin_users_invite_submit()}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setShowInviteForm(false)
                    setInviteForm({ first_name: '', last_name: '', email: '', role: 'member' })
                    setInviteError(null)
                  }}
                >
                  {m.admin_users_cancel()}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {error && (
        <p className="text-sm text-[var(--color-destructive)]">{error}</p>
      )}

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {loading ? (
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
