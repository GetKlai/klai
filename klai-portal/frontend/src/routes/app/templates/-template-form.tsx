import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

export interface TemplateFormState {
  name: string
  description: string
  prompt_text: string
  scope: string
}

export const EMPTY_TEMPLATE_FORM: TemplateFormState = {
  name: '',
  description: '',
  prompt_text: '',
  scope: 'global',
}

interface TemplateFormPageProps {
  mode: 'new' | 'edit'
  initialForm: TemplateFormState
  slug?: string
}

export function TemplateFormPage({
  mode,
  initialForm,
  slug,
}: TemplateFormPageProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [form, setForm] = useState<TemplateFormState>(initialForm)

  const createMutation = useMutation({
    mutationFn: async (body: TemplateFormState) =>
      apiFetch('/api/app/templates', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
      void navigate({ to: '/app/templates' })
    },
  })

  const updateMutation = useMutation({
    mutationFn: async (body: Partial<TemplateFormState>) =>
      apiFetch(`/api/app/templates/${slug}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
      void navigate({ to: '/app/templates' })
    },
  })

  const isSaving = createMutation.isPending || updateMutation.isPending
  const isSaveDisabled =
    !form.name.trim() || !form.prompt_text.trim() || isSaving

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (mode === 'edit') {
      updateMutation.mutate(form)
    } else {
      createMutation.mutate(form)
    }
  }

  function handleCancel() {
    void navigate({ to: '/app/templates' })
  }

  const title =
    mode === 'edit'
      ? m.templates_form_edit_title()
      : m.templates_form_new_title()
  const submitLabel = isSaving
    ? m.templates_saving()
    : mode === 'edit'
      ? m.templates_update_button()
      : m.templates_save_button()

  const error = createMutation.error ?? updateMutation.error

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-[26px] font-display-bold text-gray-900">
          {title}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleCancel}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.templates_cancel_button()}
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="tpl-name">{m.templates_field_name_label()}</Label>
          <Input
            id="tpl-name"
            type="text"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder={m.templates_field_name_placeholder()}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="tpl-description">
            {m.templates_field_description_label()}
          </Label>
          <Input
            id="tpl-description"
            type="text"
            value={form.description}
            onChange={(e) =>
              setForm({ ...form, description: e.target.value })
            }
            placeholder={m.templates_field_description_placeholder()}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="tpl-prompt">
            {m.templates_field_prompt_label()}
          </Label>
          <textarea
            id="tpl-prompt"
            value={form.prompt_text}
            onChange={(e) =>
              setForm({ ...form, prompt_text: e.target.value })
            }
            placeholder={m.templates_field_prompt_placeholder()}
            rows={5}
            className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 resize-none"
          />
          <p className="mt-1 text-xs text-gray-400">
            {form.prompt_text.length}/5000
          </p>
        </div>

        <div className="space-y-1.5">
          <Label>{m.templates_field_scope_label()}</Label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setForm({ ...form, scope: 'global' })}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium border transition-colors ${
                form.scope === 'global'
                  ? 'bg-gray-900 text-white border-gray-900'
                  : 'border-gray-200 text-gray-700 hover:bg-gray-50'
              }`}
            >
              {m.templates_scope_organization()}
            </button>
            <button
              type="button"
              onClick={() => setForm({ ...form, scope: 'personal' })}
              className={`rounded-lg px-4 py-1.5 text-sm font-medium border transition-colors ${
                form.scope === 'personal'
                  ? 'bg-gray-900 text-white border-gray-900'
                  : 'border-gray-200 text-gray-700 hover:bg-gray-50'
              }`}
            >
              {m.templates_scope_personal()}
            </button>
          </div>
        </div>

        {error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {error instanceof Error ? error.message : String(error)}
          </p>
        )}

        <div className="pt-2">
          <Button type="submit" disabled={isSaveDisabled}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </div>
  )
}
