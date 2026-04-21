import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Plus, Trash2 } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { adminLogger } from '@/lib/logger'

export const Route = createFileRoute('/admin/domains')({
  component: AdminDomainsPage,
})

interface Domain {
  id: number
  domain: string
  created_at: string
  created_by: string
}

function AdminDomainsPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [newDomain, setNewDomain] = useState('')
  const [error, setError] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['admin-domains'],
    queryFn: async () => apiFetch<{ domains: Domain[] }>('/api/admin/domains'),
    enabled: auth.isAuthenticated,
  })

  const addMutation = useMutation({
    mutationFn: async (domain: string) =>
      apiFetch('/api/admin/domains', {
        method: 'POST',
        body: JSON.stringify({ domain }),
      }),
    onSuccess: () => {
      setNewDomain('')
      setError('')
      void queryClient.invalidateQueries({ queryKey: ['admin-domains'] })
      adminLogger.info('Domain added', { domain: newDomain })
    },
    onError: (err: Error) => {
      if (err.message.includes('Free email')) setError(m.admin_domains_error_free())
      else if (err.message.includes('Invalid')) setError(m.admin_domains_error_invalid())
      else if (err.message.includes('already')) setError(m.admin_domains_error_duplicate())
      else setError(err.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: number) =>
      apiFetch(`/api/admin/domains/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-domains'] })
    },
  })

  const domains = data?.domains ?? []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{m.admin_domains_title()}</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_domains_title()}</CardTitle>
          <CardDescription>{m.admin_domains_description()}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (newDomain.trim()) addMutation.mutate(newDomain.trim())
            }}
            className="flex gap-2"
          >
            <Input
              value={newDomain}
              onChange={(e) => { setNewDomain(e.target.value); setError('') }}
              placeholder={m.admin_domains_add_placeholder()}
              className="flex-1"
            />
            <Button type="submit" disabled={!newDomain.trim() || addMutation.isPending}>
              <Plus className="mr-1 h-4 w-4" />
              {m.admin_domains_add()}
            </Button>
          </form>

          {error && (
            <p className="text-sm text-[var(--color-destructive)]">{error}</p>
          )}

          {isLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">...</p>
          ) : domains.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_domains_empty()}</p>
          ) : (
            <table className="w-full text-sm border-t border-b border-[var(--color-border)]">
              <tbody>
                {domains.map((d) => (
                  <tr key={d.id} className="border-b border-[var(--color-border)] last:border-b-0">
                    <td className="py-3 font-medium">{d.domain}</td>
                    <td className="py-3 text-right">
                      <button
                        onClick={() => deleteMutation.mutate(d.id)}
                        disabled={deleteMutation.isPending}
                        className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                        aria-label={m.admin_domains_delete_confirm()}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
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
