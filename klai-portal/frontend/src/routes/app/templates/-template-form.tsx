import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import * as m from '@/paraglide/messages'

// Shared form for new + edit. Container, buttons and field styling follow
// .claude/rules/klai/design/portal-patterns.md. The admin-gate for
// `scope="org"` is defensive UX — server-side backend (SPEC-CHAT-TEMPLATES-001)
// is the authority via HTTP 403.
//
// @MX:ANCHOR fan_in=2 — rendered by both /app/templates/new and
//     /app/templates/$slug/edit route wrappers.

export type TemplateScope = 'org' | 'personal'

export interface TemplateFormState {
  name: string
  description: string
  prompt_text: string
  scope: TemplateScope
}

export const EMPTY_TEMPLATE_FORM: TemplateFormState = {
  name: '',
  description: '',
  prompt_text: '',
  scope: 'org',
}

export const PROMPT_TEXT_MAX_LENGTH = 8000
export const NAME_MAX_LENGTH = 128
export const DESCRIPTION_MAX_LENGTH = 500
const WARNING_THRESHOLD = 7800

interface TemplateFormPageProps {
  mode: 'new' | 'edit'
  initialForm: TemplateFormState
  /** When editing, the slug of the existing template (used for PATCH). */
  slug?: string
  /** Path to navigate back to on success / cancel. Defaults to `/app/templates`. */
  backPath?: '/app/templates' | '/admin/templates'
}

interface TemplatePayload {
  name: string
  description: string | null
  prompt_text: string
  scope: TemplateScope
}

function toPayload(form: TemplateFormState): TemplatePayload {
  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    prompt_text: form.prompt_text,
    scope: form.scope,
  }
}

// Map a backend HTTP error onto the user-facing paraglide key. Defensive:
// unknown status codes fall back to the generic message.
function resolveBackendError(status: number, detail: string | undefined): string {
  if (status === 403) {
    return m.templates_form_error_org_admin_only()
  }
  if (status === 409) {
    return m.templates_form_error_slug_conflict()
  }
  if (status === 400 && detail && detail.toLowerCase().includes('prompt_text')) {
    return m.templates_form_error_prompt_too_long()
  }
  return m.templates_form_error_generic()
}

export function TemplateFormPage({
  mode,
  initialForm,
  slug,
  backPath = '/app/templates',
}: TemplateFormPageProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: currentUser } = useCurrentUser()
  const isAdmin = currentUser?.isAdmin ?? false

  // Non-admins are forced to personal scope. Do NOT mutate initialForm in
  // place — we honour its scope on the first render so the edit flow can
  // inspect an org-scope template without accidentally flipping it.
  const [form, setForm] = useState<TemplateFormState>(() => {
    if (mode === 'new' && !isAdmin && initialForm.scope === 'org') {
      return { ...initialForm, scope: 'personal' }
    }
    return initialForm
  })
  const [errorKey, setErrorKey] = useState<string | null>(null)

  const promptLength = form.prompt_text.length
  const promptOverLimit = promptLength > PROMPT_TEXT_MAX_LENGTH
  const promptWarning = promptLength >= WARNING_THRESHOLD && !promptOverLimit

  const createMutation = useMutation({
    mutationFn: async (body: TemplatePayload) =>
      apiFetch('/api/app/templates', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => invalidateAndLeave(),
    onError: (err: unknown) => setErrorKey(asErrorMessage(err)),
  })

  const updateMutation = useMutation({
    mutationFn: async (body: TemplatePayload) =>
      apiFetch(`/api/app/templates/${slug}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: () => invalidateAndLeave(),
    onError: (err: unknown) => setErrorKey(asErrorMessage(err)),
  })

  function invalidateAndLeave() {
    void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
    void queryClient.invalidateQueries({ queryKey: ['app-templates-for-bar'] })
    void queryClient.invalidateQueries({ queryKey: ['kb-preference'] })
    void navigate({ to: backPath })
  }

  function asErrorMessage(err: unknown): string {
    if (err instanceof ApiError) {
      return resolveBackendError(err.status, err.message)
    }
    return m.templates_form_error_generic()
  }

  function validate(): string | null {
    if (!form.name.trim()) return m.templates_form_error_name_required()
    if (!form.prompt_text.trim()) return m.templates_form_error_prompt_required()
    if (promptOverLimit) return m.templates_form_error_prompt_too_long()
    return null
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setErrorKey(null)
    const clientError = validate()
    if (clientError) {
      setErrorKey(clientError)
      return
    }
    const payload = toPayload(form)
    if (mode === 'new') {
      createMutation.mutate(payload)
    } else {
      updateMutation.mutate(payload)
    }
  }

  const isSaving = createMutation.isPending || updateMutation.isPending
  const submitLabel = isSaving ? m.templates_form_saving() : m.templates_form_submit()
  const title = mode === 'new' ? m.templates_form_new_title() : m.templates_form_edit_title()

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="mb-2">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">{title}</h1>
      </div>
      <p className="text-sm text-gray-400 mb-6">{m.templates_form_subtitle()}</p>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="template-name">{m.templates_form_name_label()}</Label>
          <Input
            id="template-name"
            value={form.name}
            maxLength={NAME_MAX_LENGTH}
            placeholder={m.templates_form_name_placeholder()}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            required
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="template-description">{m.templates_form_description_label()}</Label>
          <Input
            id="template-description"
            value={form.description}
            maxLength={DESCRIPTION_MAX_LENGTH}
            placeholder={m.templates_form_description_placeholder()}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          />
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="template-prompt">{m.templates_form_prompt_label()}</Label>
            <span
              className={
                promptOverLimit
                  ? 'text-xs text-[var(--color-destructive)]'
                  : promptWarning
                    ? 'text-xs text-amber-600'
                    : 'text-xs text-gray-400'
              }
              data-testid="prompt-char-count"
            >
              {m.templates_form_prompt_char_count({ current: String(promptLength) })}
            </span>
          </div>
          <textarea
            id="template-prompt"
            value={form.prompt_text}
            placeholder={m.templates_form_prompt_placeholder()}
            onChange={(e) => setForm((f) => ({ ...f, prompt_text: e.target.value }))}
            className="w-full min-h-[200px] max-h-[480px] rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)] resize-y"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="template-scope">{m.templates_form_scope_label()}</Label>
          <Select
            id="template-scope"
            value={form.scope}
            onChange={(e) => setForm((f) => ({ ...f, scope: e.target.value as TemplateScope }))}
          >
            <option value="org" disabled={!isAdmin} title={!isAdmin ? m.templates_form_scope_org_disabled_tooltip() : undefined}>
              {m.templates_list_scope_org()}
            </option>
            <option value="personal">{m.templates_list_scope_personal()}</option>
          </Select>
          {!isAdmin && (
            <p className="text-xs text-gray-400">{m.templates_form_scope_org_disabled_tooltip()}</p>
          )}
        </div>

        {errorKey && (
          <p className="text-sm text-[var(--color-destructive)]" role="alert">
            {errorKey}
          </p>
        )}

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={isSaving}
            className="rounded-full bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitLabel}
          </button>
          <button
            type="button"
            onClick={() => void navigate({ to: backPath })}
            className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
          >
            {m.templates_form_cancel()}
          </button>
        </div>
      </form>
    </div>
  )
}
