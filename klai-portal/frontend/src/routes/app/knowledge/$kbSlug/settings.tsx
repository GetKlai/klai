import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { DeleteKbModal } from '@/components/ui/delete-kb-modal'
import type { KnowledgeBase, MembersResponse, KBStats } from './-kb-types'
import { kbQueryKeys } from '@/lib/kb-query-keys'

export const Route = createFileRoute('/app/knowledge/$kbSlug/settings')({
  component: SettingsTab,
})

function SettingsTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [showSaved, setShowSaved] = useState(false)

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: kbQueryKeys.knowledgeBase(kbSlug),
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`),
    enabled: auth.isAuthenticated,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated && !!kb,
  })

  const { user: currentUser } = useCurrentUser()
  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwnerRole = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isAdmin = currentUser?.isAdmin === true
  const isOwner = isCreator || isOwnerRole || isAdmin
  // SPEC-PORTAL-KB-OWNERSHIP-001 REQ-1.1 - admins who are NOT the creator
  // get the typed-confirmation override pad. Owners + creators continue to
  // use the existing slug-typed UX.
  const isAdminOverride = isAdmin && !isCreator && !isOwnerRole
  const creatorMember = members?.users.find((u) => u.user_id === kb?.created_by)
  const creatorDisplayName =
    creatorMember?.display_name ?? creatorMember?.email ?? kb?.created_by ?? null

  const [deleteModalOpen, setDeleteModalOpen] = useState(false)

  const { data: stats } = useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`),
    enabled: auth.isAuthenticated && !!kb,
  })

  // Sync form state when KB data loads
  useEffect(() => {
    if (kb) {
      setName(kb.name)
      setDescription(kb.description ?? '')
    }
  }, [kb])

  const updateMutation = useMutation({
    mutationFn: async (body: { name?: string; description?: string }) => {
      return apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.knowledgeBase(kbSlug) })
      setShowSaved(true)
      setTimeout(() => setShowSaved(false), 2000)
    },
  })

  if (!kb) return null

  const hasChanges = name !== kb.name || description !== (kb.description ?? '')
  const canSave = isOwner && hasChanges && name.trim().length > 0

  return (
    <div className="space-y-6">
      {/* General settings */}
      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-gray-900">
          {m.knowledge_settings_general_heading()}
        </h2>
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
              disabled={!isOwner}
              required
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="kb-description">{m.knowledge_settings_description_label()}</Label>
            <textarea
              id="kb-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={!isOwner}
              rows={3}
              className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50 resize-none"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="kb-slug">{m.knowledge_settings_slug_label()}</Label>
            <Input
              id="kb-slug"
              type="text"
              value={kb.slug}
              disabled
              className="bg-[var(--color-secondary)] text-gray-400"
            />
            <p className="text-xs text-gray-400">
              {m.knowledge_settings_slug_hint()}
            </p>
          </div>

          {updateMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">
              {String(updateMutation.error)}
            </p>
          )}

          {isOwner && (
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
          )}
          {!isOwner && (
            <p className="text-xs text-gray-400 pt-2">
              Alleen de eigenaar of een admin kan deze velden wijzigen.
            </p>
          )}
        </form>
      </div>

      {/* Members */}
      {members && members.users.length > 0 && (
        <div className="space-y-2 border-t border-gray-200 pt-6">
          <h2 className="text-sm font-semibold text-gray-900">Leden</h2>
          <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
            {members.users.map((u) => (
              <div key={u.user_id} className="flex items-center justify-between gap-3 py-2.5 px-2">
                <span className="text-sm text-gray-900 truncate">
                  {u.email || u.user_id}
                </span>
                <span className="text-xs text-gray-400 capitalize shrink-0">{u.role}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Danger zone - owner / admin only */}
      {isOwner && (
        <div className="space-y-2 border-t border-gray-200 pt-6">
          <h2 className="text-sm font-semibold text-[var(--color-destructive)]">
            {m.knowledge_settings_danger_heading()}
          </h2>
          <p className="text-sm text-gray-400">
            {m.knowledge_settings_danger_description()}
          </p>
          <div className="pt-2">
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setDeleteModalOpen(true)}
            >
              {m.knowledge_settings_delete_button()}
            </Button>
          </div>
          <DeleteKbModal
            open={deleteModalOpen}
            onOpenChange={setDeleteModalOpen}
            kbSlug={kb.slug}
            kbName={kb.name}
            itemCount={stats?.docs_count ?? null}
            connectorCount={stats?.connector_count ?? 0}
            hasGitea={!!kb.gitea_repo_slug}
            hasDocs={kb.docs_enabled}
            mode={isAdminOverride ? 'admin-override' : 'self'}
            creatorName={creatorDisplayName}
          />
        </div>
      )}
    </div>
  )
}
