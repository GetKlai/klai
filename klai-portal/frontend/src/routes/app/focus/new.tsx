import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/focus/new')({
  component: () => (
    <ProductGuard product="chat">
      <NewFocusPage />
    </ProductGuard>
  ),
})

const FOCUS_BASE = '/api/research/v1'

type Scope = 'personal' | 'org'
type Mode = 'narrow' | 'broad' | 'web'

interface NotebookForm {
  name: string
  description: string
  scope: Scope
  default_mode: Mode
}

interface NotebookResponse {
  id: string
}

function NewFocusPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [form, setForm] = useState<NotebookForm>({
    name: '',
    description: '',
    scope: 'personal',
    default_mode: 'narrow',
  })

  const createMutation = useMutation({
    mutationFn: async (data: NotebookForm) => {
      return apiFetch<NotebookResponse>(`${FOCUS_BASE}/notebooks`, {
        method: 'POST',
        body: JSON.stringify({
          name: data.name,
          description: data.description || null,
          scope: data.scope,
          default_mode: data.default_mode,
        }),
      })
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['focus-notebooks'] })
      void navigate({ to: '/app/focus/$notebookId', params: { notebookId: data.id } })
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    createMutation.mutate(form)
  }

  function handleCancel() {
    void navigate({ to: '/app/focus' })
  }

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">
          {m.app_focus_new_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={handleCancel}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_focus_back()}
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="name">{m.app_focus_notebook_name_label()}</Label>
          <Input
            id="name"
            type="text"
            required
            value={form.name}
            onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="description">
            {m.app_focus_notebook_description_label()}
          </Label>
          <Input
            id="description"
            type="text"
            placeholder={m.app_focus_notebook_description_optional()}
            value={form.description}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, description: e.target.value }))
            }
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="default_mode">{m.app_focus_notebook_mode_label()}</Label>
            <Select
              id="default_mode"
              value={form.default_mode}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, default_mode: e.target.value as Mode }))
              }
            >
              <option value="narrow">{m.app_focus_notebook_mode_narrow()}</option>
              <option value="broad">{m.app_focus_notebook_mode_broad()}</option>
              <option value="web">{m.app_focus_notebook_mode_web()}</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="scope">{m.app_focus_notebook_scope_label()}</Label>
            <Select
              id="scope"
              value={form.scope}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, scope: e.target.value as Scope }))
              }
            >
              <option value="personal">{m.app_focus_notebook_scope_personal()}</option>
              <option value="org">{m.app_focus_notebook_scope_org()}</option>
            </Select>
          </div>
        </div>

        {createMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {createMutation.error instanceof Error
              ? createMutation.error.message
              : m.app_focus_create_submit() + ' mislukt'}
          </p>
        )}

        <div className="pt-2">
          <Button type="submit" disabled={createMutation.isPending}>
            {createMutation.isPending
              ? m.app_focus_loading()
              : m.app_focus_create_submit()}
          </Button>
        </div>
      </form>
    </div>
  )
}
