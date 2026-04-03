import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { Check } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DeleteKbModal } from '@/components/ui/delete-kb-modal'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import type { KnowledgeBase, KBStats, MembersResponse } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/settings')({
  component: SettingsTab,
})

function SettingsTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [deleteModalOpen, setDeleteModalOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [showSaved, setShowSaved] = useState(false)

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token),
    enabled: !!token,
  })

  const { data: stats } = useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`, token),
    enabled: !!token && !!kb,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`, token),
    enabled: !!token && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isOwner = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

  // Sync form state when KB data loads
  useEffect(() => {
    if (kb) {
      setName(kb.name)
      setDescription(kb.description ?? '')
    }
  }, [kb])

  const updateMutation = useMutation({
    mutationFn: async (body: { name?: string; description?: string }) => {
      return apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-base', kbSlug] })
      setShowSaved(true)
      setTimeout(() => setShowSaved(false), 2000)
    },
  })

  if (!kb || !isOwner) return null

  const hasChanges = name !== kb.name || description !== (kb.description ?? '')
  const canSave = hasChanges && name.trim().length > 0

  return (
    <div className="space-y-6">
      {/* General settings */}
      <Card>
        <CardHeader>
          <CardTitle>{m.knowledge_settings_general_heading()}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (!canSave) return
              const body: { name?: string; description?: string } = {}
              if (name !== kb.name) body.name = name.trim()
              if (description !== (kb.description ?? '')) body.description = description.trim()
              updateMutation.mutate(body)
            }}
            className="space-y-4"
          >
            <div className="space-y-1.5">
              <Label htmlFor="kb-name">{m.knowledge_settings_name_label()}</Label>
              <Input
                id="kb-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="kb-description">{m.knowledge_settings_description_label()}</Label>
              <Input
                id="kb-description"
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="kb-slug">{m.knowledge_settings_slug_label()}</Label>
              <Input
                id="kb-slug"
                type="text"
                value={kb.slug}
                disabled
                className="bg-[var(--color-secondary)] text-[var(--color-muted-foreground)]"
              />
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.knowledge_settings_slug_hint()}
              </p>
            </div>

            {updateMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {String(updateMutation.error)}
              </p>
            )}

            <div className="flex items-center gap-3 pt-2">
              <Button type="submit" size="sm" disabled={!canSave || updateMutation.isPending}>
                {updateMutation.isPending ? m.knowledge_settings_saving() : m.knowledge_settings_save()}
              </Button>
              {showSaved && (
                <span className="flex items-center gap-1 text-sm text-[var(--color-success)]">
                  <Check className="h-4 w-4" />
                  {m.knowledge_settings_saved()}
                </span>
              )}
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Danger zone */}
      <div className="rounded-lg border border-[var(--color-destructive)]/50 p-6">
        <h3 className="text-sm font-semibold text-[var(--color-destructive)] mb-1">
          {m.knowledge_settings_danger_heading()}
        </h3>
        <p className="text-sm text-[var(--color-muted-foreground)] mb-4">
          {m.knowledge_settings_danger_description()}
        </p>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => setDeleteModalOpen(true)}
        >
          {m.knowledge_settings_delete_button()}
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
