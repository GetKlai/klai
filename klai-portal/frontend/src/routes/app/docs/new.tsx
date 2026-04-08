import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/docs/new')({
  component: NewKBPage,
})

const DOCS_BASE = '/docs/api'

function getOrgSlug(): string {
  return window.location.hostname.split('.')[0]
}

function NewKBPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const orgSlug = getOrgSlug()

  const [name, setName] = useState('')
  const [visibility, setVisibility] = useState<'private' | 'public'>('private')

  const createMutation = useMutation({
    mutationFn: async () => {
      return apiFetch<{ slug: string }>(`${DOCS_BASE}/orgs/${orgSlug}/kbs`, token, {
        method: 'POST',
        body: JSON.stringify({ name, visibility }),
      })
    },
    onSuccess: (kb) => {
      void queryClient.invalidateQueries({ queryKey: ['docs-kbs', orgSlug] })
      void navigate({ to: '/app/docs/$kbSlug', params: { kbSlug: kb.slug } })
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    createMutation.mutate()
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-[var(--color-foreground)]">
          {m.docs_kbs_new()}
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
                placeholder={m.docs_kb_name_placeholder()}
                autoFocus
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="kb-visibility">{m.docs_kb_visibility_label()}</Label>
              <Select
                id="kb-visibility"
                value={visibility}
                onChange={(e) => setVisibility(e.target.value as 'private' | 'public')}
                className="max-w-xs"
              >
                <option value="private">{m.docs_kb_visibility_private()}</option>
                <option value="public">{m.docs_kb_visibility_public()}</option>
              </Select>
            </div>

            {createMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {createMutation.error instanceof Error
                  ? createMutation.error.message
                  : 'Aanmaken mislukt'}
              </p>
            )}

            <div className="pt-2">
              <Button type="submit" disabled={!name.trim() || createMutation.isPending}>
                {createMutation.isPending ? 'Bezig...' : m.docs_kb_create()}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
