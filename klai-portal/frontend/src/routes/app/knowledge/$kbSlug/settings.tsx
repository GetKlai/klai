import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { DeleteKbModal } from '@/components/ui/delete-kb-modal'
import { API_BASE } from '@/lib/api'
import type { KnowledgeBase, KBStats, MembersResponse } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/settings')({
  component: SettingsTab,
})

function SettingsTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const [deleteModalOpen, setDeleteModalOpen] = useState(false)

  // Reuse cached data from parent layout
  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('KB laden mislukt')
      return res.json() as Promise<KnowledgeBase>
    },
    enabled: !!token,
  })

  const { data: stats } = useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/stats`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Stats laden mislukt')
      return res.json() as Promise<KBStats>
    },
    enabled: !!token && !!kb,
  })

  // Guard: only owners can see this tab (also enforced by route.tsx tab visibility)
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Members laden mislukt')
      return res.json() as Promise<MembersResponse>
    },
    enabled: !!token && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isOwner = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

  if (!kb || !isOwner) return null

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-[var(--color-destructive)]/50 p-6">
        <h3 className="text-sm font-semibold text-[var(--color-destructive)] mb-1">
          Gevaarlijke zone
        </h3>
        <p className="text-sm text-[var(--color-muted-foreground)] mb-4">
          Het verwijderen van deze knowledge base kan niet ongedaan worden gemaakt.
        </p>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => setDeleteModalOpen(true)}
        >
          Verwijder knowledge base
        </Button>
        <DeleteKbModal
          open={deleteModalOpen}
          onOpenChange={setDeleteModalOpen}
          kbSlug={kb.slug}
          kbName={kb.name}
          itemCount={stats?.docs_count ?? null}
          connectorCount={stats?.connector_count ?? 0}
          hasGitea={!!kb.gitea_repo_slug}
          hasDocs={kb.docs_enabled}
          token={auth.user?.access_token ?? ''}
        />
      </div>
    </div>
  )
}
