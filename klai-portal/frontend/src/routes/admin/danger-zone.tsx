import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Skull } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { DeleteOrgModal } from '@/components/ui/delete-org-modal'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/danger-zone')({
  component: DangerZonePage,
})

interface OrgMe {
  slug: string
  name: string
}

// @MX:NOTE: Owner-only page - access gated by isAdmin check via useProtectedRoute.
// @MX:SPEC: SPEC-INFRA-TENANT-DELETE-001 Phase 10 R10
function DangerZonePage() {
  const { canRender } = useProtectedRoute({
    requireAdmin: true,
    noRoleFallback: '/admin',
  })
  const auth = useAuth()
  const navigate = useNavigate()
  const [modalOpen, setModalOpen] = useState(false)

  const { data: org } = useQuery({
    queryKey: ['admin-org-me'],
    queryFn: async () => apiFetch<OrgMe>('/api/admin/org/me'),
    enabled: auth.isAuthenticated,
  })

  if (!canRender) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-lg px-6 pt-4 pb-10 space-y-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.danger_zone_heading()}
        </h1>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void navigate({ to: '/admin' })}
        >
          {m.danger_zone_back()}
        </Button>
      </div>
      <p className="text-sm text-gray-400 -mt-4">{m.danger_zone_subtitle()}</p>

      <Card className="border-[var(--color-destructive)]/30">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-[var(--color-destructive)]">
            <Skull className="h-5 w-5" />
            {m.danger_zone_delete_card_title()}
          </CardTitle>
          <CardDescription>
            {m.danger_zone_delete_card_description()}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="destructive"
            onClick={() => setModalOpen(true)}
          >
            {m.danger_zone_delete_button()}
          </Button>
        </CardContent>
      </Card>

      {org && (
        <DeleteOrgModal
          open={modalOpen}
          onOpenChange={setModalOpen}
          orgSlug={org.slug}
          orgName={org.name}
        />
      )}
    </div>
  )
}
