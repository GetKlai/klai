import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import type { KnowledgeBase, MembersResponse } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/settings')({
  component: SettingsTab,
})

function SettingsTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [showSaved, setShowSaved] = useState(false)

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token),
    enabled: !!token,
  })

  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`, token),
    enabled: !!token && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

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
            <Label htmlFor="kb-name" className="text-gray-900">{m.knowledge_settings_name_label()}</Label>
            <Input
              id="kb-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="rounded-lg border-gray-200"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="kb-description" className="text-gray-900">{m.knowledge_settings_description_label()}</Label>
            <textarea
              id="kb-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 disabled:cursor-not-allowed disabled:opacity-50 resize-none"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="kb-slug" className="text-gray-900">{m.knowledge_settings_slug_label()}</Label>
            <Input
              id="kb-slug"
              type="text"
              value={kb.slug}
              disabled
              className="bg-gray-50 text-gray-400 rounded-lg border-gray-200"
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

          <div className="flex items-center gap-3 pt-2">
            <Button
              type="submit"
              size="sm"
              disabled={!canSave || updateMutation.isPending}
              className="rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50"
            >
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
      </div>
    </div>
  )
}
