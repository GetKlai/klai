import { createFileRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useAuth } from '@/lib/auth'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/integrations')({
  component: IntegrationsPage,
})

interface ConnectedApp {
  id: number
  client_name: string
  application_type: 'native' | 'web' | 'unknown'
  scopes: string[]
  created_at: string
  last_used_at: string | null
  expires_at: string
  refresh_expires_at: string | null
  revoked_at: string | null
}

function IntegrationsPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [confirmingId, setConfirmingId] = useState<number | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['mcp-tokens'],
    queryFn: async () => {
      return await apiFetch<ConnectedApp[]>('/api/me/mcp-tokens')
    },
    enabled: auth.isAuthenticated,
  })

  const revokeMutation = useMutation({
    mutationFn: async (tokenId: number) => {
      await apiFetch(`/api/me/mcp-tokens/${tokenId}`, { method: 'DELETE' })
      return tokenId
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mcp-tokens'] })
      setConfirmingId(null)
    },
  })

  const activeTokens = (data ?? []).filter((t) => t.revoked_at === null)
  const hasNoTokens = !isLoading && activeTokens.length === 0

  return (
    <div className="mx-auto max-w-2xl px-6 py-10 space-y-6">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.integrations_heading()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.integrations_subtitle()}
        </p>
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-[var(--color-destructive)]">
              {m.integrations_error_load()}
            </p>
          </CardContent>
        </Card>
      )}

      {isLoading && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.integrations_loading()}
            </p>
          </CardContent>
        </Card>
      )}

      {hasNoTokens && (
        <Card>
          <CardHeader>
            <CardTitle>{m.integrations_empty_title()}</CardTitle>
            <CardDescription>{m.integrations_empty_description()}</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="rounded-lg bg-[var(--color-muted)] p-4 font-mono text-sm">
              https://mcp.getklai.com/mcp
            </div>
            <p className="mt-3 text-sm text-[var(--color-muted-foreground)]">
              {m.integrations_empty_hint()}
            </p>
          </CardContent>
        </Card>
      )}

      {activeTokens.map((token) => (
        <Card key={token.id} data-help-id="integration-card">
          <CardHeader>
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle>{token.client_name}</CardTitle>
                <CardDescription>
                  {token.application_type === 'native'
                    ? m.integrations_type_native()
                    : token.application_type === 'web'
                      ? m.integrations_type_web()
                      : m.integrations_type_unknown()}
                </CardDescription>
              </div>
              {confirmingId === token.id ? (
                <div className="flex gap-2 shrink-0">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setConfirmingId(null)}
                    disabled={revokeMutation.isPending}
                  >
                    {m.integrations_cancel()}
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => revokeMutation.mutate(token.id)}
                    disabled={revokeMutation.isPending}
                  >
                    {revokeMutation.isPending
                      ? m.integrations_revoking()
                      : m.integrations_confirm_revoke()}
                  </Button>
                </div>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setConfirmingId(token.id)}
                >
                  {m.integrations_revoke()}
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              <div className="flex gap-3">
                <dt className="w-32 shrink-0 text-[var(--color-muted-foreground)]">
                  {m.integrations_field_created()}
                </dt>
                <dd>{formatDate(token.created_at)}</dd>
              </div>
              <div className="flex gap-3">
                <dt className="w-32 shrink-0 text-[var(--color-muted-foreground)]">
                  {m.integrations_field_last_used()}
                </dt>
                <dd>
                  {token.last_used_at
                    ? formatDate(token.last_used_at)
                    : m.integrations_never_used()}
                </dd>
              </div>
              <div className="flex gap-3">
                <dt className="w-32 shrink-0 text-[var(--color-muted-foreground)]">
                  {m.integrations_field_expires()}
                </dt>
                <dd>{formatDate(token.expires_at)}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function formatDate(iso: string): string {
  try {
    const date = new Date(iso)
    return date.toLocaleString('nl-NL', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}
