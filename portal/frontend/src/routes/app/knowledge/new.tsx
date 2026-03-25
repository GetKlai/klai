import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Brain } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/knowledge/new')({
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgeNewPage />
    </ProductGuard>
  ),
})

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function KnowledgeNewPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugManuallyEdited, setSlugManuallyEdited] = useState(false)
  const [visibility, setVisibility] = useState<'internal' | 'public'>('internal')
  const [errorKey, setErrorKey] = useState<'conflict' | 'generic' | null>(null)

  function handleNameChange(value: string) {
    setName(value)
    if (!slugManuallyEdited) {
      setSlug(slugify(value))
    }
  }

  function handleSlugChange(value: string) {
    setSlugManuallyEdited(true)
    setSlug(slugify(value))
  }

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, slug, visibility }),
      })
      if (res.status === 409) {
        throw new Error('conflict')
      }
      if (!res.ok) {
        throw new Error('generic')
      }
      return res.json() as Promise<{ slug: string }>
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug: data.slug } })
    },
    onError: (err: Error) => {
      setErrorKey(err.message === 'conflict' ? 'conflict' : 'generic')
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setErrorKey(null)
    mutate()
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center gap-3 mb-6">
        <Brain className="h-6 w-6 text-[var(--color-purple-deep)]" />
        <h1 className="font-serif text-xl font-bold text-[var(--color-purple-deep)]">
          {m.knowledge_new_heading()}
        </h1>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-name">{m.knowledge_new_name_label()}</Label>
          <Input
            id="kb-name"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder={m.knowledge_new_name_placeholder()}
            required
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-slug">{m.knowledge_new_slug_label()}</Label>
          <Input
            id="kb-slug"
            value={slug}
            onChange={(e) => handleSlugChange(e.target.value)}
            required
            pattern="[a-z0-9-]+"
          />
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.knowledge_new_slug_hint()}
          </p>
          {errorKey === 'conflict' && (
            <p className="text-xs text-[var(--color-destructive)]">
              {m.knowledge_new_slug_conflict()}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-visibility">{m.knowledge_new_visibility_label()}</Label>
          <Select
            id="kb-visibility"
            value={visibility}
            onChange={(e) => setVisibility(e.target.value as 'internal' | 'public')}
          >
            <option value="internal">{m.knowledge_new_visibility_internal()}</option>
            <option value="public">{m.knowledge_new_visibility_public()}</option>
          </Select>
        </div>

        {errorKey === 'generic' && (
          <p className="text-sm text-[var(--color-destructive)]">{m.knowledge_new_error()}</p>
        )}

        <div className="flex gap-3 pt-2">
          <Button type="submit" disabled={isPending || !name || !slug}>
            {m.knowledge_new_submit()}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void navigate({ to: '/app/knowledge' })}
          >
            {m.knowledge_new_cancel()}
          </Button>
        </div>
      </form>
    </div>
  )
}
