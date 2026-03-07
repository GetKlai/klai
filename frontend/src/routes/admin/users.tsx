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

export const Route = createFileRoute('/admin/users')({
  component: UsersPage,
})

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  created_at: string
}

interface InviteForm {
  first_name: string
  last_name: string
  email: string
}

function formatDutchDate(isoString: string): string {
  const date = new Date(isoString)
  return date.toLocaleDateString('nl-NL', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
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
  })
  const [inviteLoading, setInviteLoading] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchUsers() {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/api/admin/users`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`Fout bij ophalen gebruikers (${res.status})`)
        const data = await res.json()
        setUsers(data.users)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Er is een fout opgetreden.')
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
    if (!window.confirm(`Weet je zeker dat je ${name} wilt verwijderen?`)) return

    setUsers((prev) => prev.filter((u) => u.zitadel_user_id !== user.zitadel_user_id))

    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_BASE_URL}/api/admin/users/${user.zitadel_user_id}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        }
      )
      if (!res.ok) throw new Error(`Verwijderen mislukt (${res.status})`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verwijderen mislukt.')
      setUsers((prev) => [...prev, user])
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
      if (!res.ok) throw new Error(`Uitnodiging mislukt (${res.status})`)

      setInviteForm({ first_name: '', last_name: '', email: '' })
      setShowInviteForm(false)

      const refreshRes = await fetch(`${import.meta.env.VITE_API_BASE_URL}/api/admin/users`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (refreshRes.ok) {
        const data = await refreshRes.json()
        setUsers(data.users)
      }
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : 'Uitnodiging mislukt.')
    } finally {
      setInviteLoading(false)
    }
  }

  const columns = [
    columnHelper.accessor((row) => `${row.first_name} ${row.last_name}`, {
      id: 'naam',
      header: 'Naam',
      cell: (info) => info.getValue(),
    }),
    columnHelper.accessor('email', {
      header: 'E-mail',
      cell: (info) => info.getValue(),
    }),
    columnHelper.accessor('created_at', {
      header: 'Lid sinds',
      cell: (info) => formatDutchDate(info.getValue()),
    }),
    columnHelper.display({
      id: 'acties',
      header: 'Acties',
      cell: ({ row }) => {
        const user = row.original
        const isSelf = user.zitadel_user_id === currentUserId
        return (
          <Button
            variant="destructive"
            size="sm"
            disabled={isSelf}
            onClick={() => handleDelete(user)}
          >
            Verwijderen
          </Button>
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
            Gebruikers
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            Beheer de gebruikers in jouw organisatie.
          </p>
        </div>
        <Button
          onClick={() => {
            setShowInviteForm((prev) => !prev)
            setInviteError(null)
          }}
        >
          Gebruiker uitnodigen
        </Button>
      </div>

      {showInviteForm && (
        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleInvite} className="space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1">
                  <label className="text-sm font-medium text-[var(--color-purple-deep)]">
                    Voornaam
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
                    Achternaam
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
              <div className="space-y-1">
                <label className="text-sm font-medium text-[var(--color-purple-deep)]">
                  E-mailadres
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
              {inviteError && (
                <p className="text-sm text-[var(--color-destructive)]">{inviteError}</p>
              )}
              <div className="flex gap-2">
                <Button type="submit" disabled={inviteLoading}>
                  {inviteLoading ? 'Versturen...' : 'Uitnodiging versturen'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setShowInviteForm(false)
                    setInviteForm({ first_name: '', last_name: '', email: '' })
                    setInviteError(null)
                  }}
                >
                  Annuleren
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
              Bezig met laden...
            </p>
          ) : users.length === 0 ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              Nog geen gebruikers.
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
