import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Users,
  User,
  ArrowLeft,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/knowledge/new')({
  component: () => (
    <ProductGuard product="knowledge">
      <NewKnowledgeBasePage />
    </ProductGuard>
  ),
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

function NewKnowledgeBasePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [errorKey, setErrorKey] = useState<'conflict' | 'generic' | null>(null)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [ownerType, setOwnerType] = useState<'org' | 'user'>('org')

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const slug = slugify(name)

      return apiFetch<{ slug: string }>(`/api/app/knowledge-bases`, token, {
        method: 'POST',
        body: JSON.stringify({
          name,
          slug,
          description: description || undefined,
          visibility: 'internal',
          owner_type: ownerType,
          default_org_role: ownerType === 'org' ? 'viewer' : undefined,
        }),
      })
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug: result.slug } })
    },
    onError: (err: Error) => {
      setErrorKey(err instanceof ApiError && err.status === 409 ? 'conflict' : 'generic')
    },
  })

  const canSubmit = name.trim().length > 0

  return (
    <div
      className="mx-auto max-w-3xl px-6 py-10"
      style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-semibold text-gray-900">
          {m.knowledge_new_heading()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50"
          onClick={() => void navigate({ to: '/app/knowledge' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.knowledge_wizard_cancel()}
        </Button>
      </div>

      {/* Single-page form */}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!canSubmit) return
          setErrorKey(null)
          mutate()
        }}
        className="space-y-6"
      >
        {/* Name */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-name" className="text-gray-900">
            {m.knowledge_new_name_label()}
          </Label>
          <Input
            id="kb-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={m.knowledge_new_name_placeholder()}
            className="rounded-lg border-gray-200"
          />
          {errorKey === 'conflict' && (
            <p className="text-xs text-[var(--color-destructive)]">
              {m.knowledge_new_slug_conflict()}
            </p>
          )}
        </div>

        {/* Description */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-description" className="text-gray-900">
            {m.knowledge_wizard_description_label()}
            <span className="ml-1 text-gray-400 font-normal">({m.knowledge_wizard_description_placeholder()})</span>
          </Label>
          <textarea
            id="kb-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={m.knowledge_wizard_description_placeholder()}
            rows={3}
            className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 disabled:cursor-not-allowed disabled:opacity-50 resize-none"
          />
        </div>

        {/* Scope toggle */}
        <div className="flex flex-col gap-1.5">
          <Label className="text-gray-900">{m.knowledge_new_scope_label()}</Label>
          <div className="grid grid-cols-2 gap-3">
            {(['org', 'user'] as const).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setOwnerType(type)}
                className={[
                  'flex flex-col items-start gap-1 rounded-lg border p-4 text-left transition-all',
                  ownerType === type
                    ? 'border-gray-900 bg-gray-50 ring-1 ring-gray-900'
                    : 'border-gray-200 hover:bg-gray-50',
                ].join(' ')}
              >
                {type === 'org' ? (
                  <Users className="h-4 w-4 text-gray-700" />
                ) : (
                  <User className="h-4 w-4 text-gray-700" />
                )}
                <span className="text-sm font-medium text-gray-900">
                  {type === 'org' ? m.knowledge_new_scope_org() : m.knowledge_new_scope_personal()}
                </span>
                <span className="text-xs text-gray-400">
                  {type === 'org'
                    ? m.knowledge_new_scope_org_description()
                    : m.knowledge_new_scope_personal_description()}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Error */}
        {errorKey === 'generic' && (
          <p className="text-sm text-[var(--color-destructive)]">{m.knowledge_new_error()}</p>
        )}

        {/* Submit */}
        <div className="flex pt-2">
          <Button
            type="submit"
            disabled={!canSubmit || isPending}
          >
            {m.knowledge_wizard_create_button()}
          </Button>
        </div>
      </form>
    </div>
  )
}
