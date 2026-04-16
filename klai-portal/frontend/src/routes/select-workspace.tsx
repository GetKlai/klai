import { createFileRoute, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
  org_id: number
  name: string
  slug: string
}

function SelectWorkspacePage() {
  useLocale()
  const { ref } = useSearch({ from: '/select-workspace' })
  const [selectedOrg, setSelectedOrg] = useState<number | null>(null)

  const selectMutation = useMutation({
    mutationFn: async (orgId: number) => {
      const res = await fetch(`${API_BASE}/api/auth/select-workspace`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, org_id: orgId }),
      })
      if (!res.ok) throw new Error('Selection failed')
      return res.json() as Promise<{ workspace_url: string }>
    },
    onSuccess: (data) => {
      window.location.replace(data.workspace_url)
    },
    onError: (err) => {
      authLogger.error('Workspace selection failed', err)
    },
  })

  const leftContent = (
    <h1 className="text-2xl font-semibold leading-tight">
      {m.select_workspace_heading()}
    </h1>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-4">
        <div className="space-y-2">
          <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
            {m.select_workspace_heading()}
          </h2>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.select_workspace_body()}
          </p>
        </div>

        {/* Workspaces will be populated from the pending session data */}
        <div className="space-y-2">
          <Button
            onClick={() => selectMutation.mutate(selectedOrg ?? 0)}
            disabled={!selectedOrg || selectMutation.isPending}
            size="lg"
            className="w-full"
          >
            {selectMutation.isPending ? '...' : m.select_workspace_heading()}
          </Button>
        </div>
      </div>
    </AuthPageLayout>
  )
}
