import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/docs/$kbSlug_/edit')({
  component: EditKBPage,
})

const DOCS_BASE = '/docs/api'

function getOrgSlug(): string {
  return window.location.hostname.split('.')[0]
}

interface KnowledgeBase {
  id: string
  slug: string
  name: string
  visibility: 'public' | 'private'
}

function EditKBPage() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const orgSlug = getOrgSlug()

  const [name, setName] = useState('')
  const [visibility, setVisibility] = useState<'public' | 'private'>('private')

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['docs-kb', orgSlug, kbSlug, token],
    queryFn: async () => {
      const res = await fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Laden mislukt')
      const kbs: KnowledgeBase[] = await res.json()
      const found = kbs.find((k) => k.slug === kbSlug)
      if (!found) throw new Error('Kennisbank niet gevonden')
      return found
    },
    enabled: !!token,
  })

  useEffect(() => {
    if (kb) {
      setName(kb.name)
      setVisibility(kb.visibility)
    }
  }, [kb])

  const editMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, visibility }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error ?? 'Opslaan mislukt')
      }
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docs-kbs', orgSlug] })
      navigate({ to: '/app/docs' })
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    editMutation.mutate()
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.docs_kb_edit_modal_title()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/app/docs' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.docs_kb_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="kb-name">{m.docs_kb_name_label()}</Label>
              <Input
                id="kb-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="kb-visibility">Zichtbaarheid</Label>
              <Select
                id="kb-visibility"
                value={visibility}
                onChange={(e) => setVisibility(e.target.value as 'public' | 'private')}
                className="max-w-xs"
              >
                <option value="private">{m.docs_kb_visibility_private()}</option>
                <option value="public">{m.docs_kb_visibility_public()}</option>
              </Select>
            </div>

            {editMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {editMutation.error instanceof Error
                  ? editMutation.error.message
                  : 'Opslaan mislukt'}
              </p>
            )}

            <div className="pt-2">
              <Button type="submit" disabled={!name.trim() || editMutation.isPending || !kb}>
                {editMutation.isPending ? 'Bezig...' : m.docs_kb_edit_save_action()}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
