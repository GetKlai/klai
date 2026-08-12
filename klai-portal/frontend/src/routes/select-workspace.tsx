import { createFileRoute, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { API_BASE } from '@/lib/api'
import { authLogger } from '@/lib/logger'

export const Route = createFileRoute('/select-workspace')({
  component: SelectWorkspacePage,
  validateSearch: (search: Record<string, unknown>) => ({
    ref: (search.ref as string) || '',
  }),
})

interface Workspace {
  id: number
  name: string
  slug: string
  // SPEC-AUTH-010 R8: join semantics so the CTA can be labelled correctly.
  kind?: 'member' | 'domain_match'
  auto_accept?: boolean
}

function SelectWorkspacePage() {
  useLocale()
  const { ref } = useSearch({ from: '/select-workspace' })
  const [selectedOrg, setSelectedOrg] = useState<number | null>(null)

  const { data, isLoading, error: fetchError } = useQuery({
    queryKey: ['pending-session', ref],
    queryFn: async () => {
      const res = await fetch(
        `${API_BASE}/api/auth/pending-session?ref=${encodeURIComponent(ref)}`,
      )
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
      }
      return res.json() as Promise<{ orgs: Workspace[] }>
    },
    enabled: !!ref,
    retry: false,
  })

  const selectMutation = useMutation({
    mutationFn: async (orgId: number) => {
      const res = await fetch(`${API_BASE}/api/auth/select-workspace`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, org_id: orgId }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
      }
      return res.json() as Promise<
        | { kind: 'member' | 'auto_join'; redirect_url: string }
        | { kind: 'join_request_pending'; redirect_to: string }
      >
    },
    onSuccess: (result) => {
      window.location.replace('redirect_url' in result ? result.redirect_url : result.redirect_to)
    },
    onError: (err) => {
      authLogger.error('Workspace selection failed', { error: String(err) })
    },
  })

  const leftContent = (
    <h1 className="text-2xl font-semibold leading-tight">
      {m.select_workspace_heading()}
    </h1>
  )

  if (isLoading) {
    return (
      <AuthPageLayout leftContent={leftContent} showLocale>
        <div className="flex items-center justify-center py-8">
          <span className="text-sm text-gray-400">…</span>
        </div>
      </AuthPageLayout>
    )
  }

  if (fetchError || !data) {
    return (
      <AuthPageLayout leftContent={leftContent} showLocale>
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-destructive)]">
            {fetchError instanceof Error
              ? fetchError.message
              : m.select_workspace_session_expired()}
          </p>
          <Button
            variant="ghost"
            size="lg"
            className="w-full"
            onClick={() => window.location.replace('/')}
          >
            {m.forgot_back()}
          </Button>
        </div>
      </AuthPageLayout>
    )
  }

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-gray-900">
          {m.select_workspace_heading()}
        </h2>
        <p className="text-sm text-gray-400">
          {m.select_workspace_body()}
        </p>
      </div>

      <div className="space-y-2">
        {data.orgs.map((org) => (
          <button
            key={org.id}
            type="button"
            onClick={() => setSelectedOrg(org.id)}
            className={[
              'w-full rounded-xl border p-4 text-left transition-colors',
              selectedOrg === org.id
                ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/10'
                : 'border-gray-200 bg-[var(--color-card)] hover:border-[var(--color-rl-accent)]/50',
            ].join(' ')}
          >
            <span className="block font-medium text-gray-900">
              {org.name}
            </span>
            <span className="block text-xs text-gray-400">
              {org.kind === 'domain_match'
                ? org.auto_accept
                  ? m.select_workspace_join_auto_hint()
                  : m.select_workspace_request_hint()
                : org.slug}
            </span>
          </button>
        ))}
      </div>

      {selectMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {selectMutation.error instanceof Error
            ? selectMutation.error.message
            : String(selectMutation.error)}
        </p>
      )}

      <Button
        onClick={() => selectedOrg !== null && selectMutation.mutate(selectedOrg)}
        disabled={selectedOrg === null || selectMutation.isPending}
        size="lg"
        className="w-full"
      >
        {selectMutation.isPending ? '…' : ctaLabel(data.orgs, selectedOrg)}
      </Button>
    </AuthPageLayout>
  )
}

// SPEC-AUTH-010 R8: CTA reflects what selecting the workspace will do.
function ctaLabel(orgs: Workspace[], selectedOrg: number | null): string {
  const org = orgs.find((o) => o.id === selectedOrg)
  if (org?.kind === 'domain_match') {
    return org.auto_accept ? m.select_workspace_join_cta() : m.select_workspace_request_cta()
  }
  return m.select_workspace_continue()
}
