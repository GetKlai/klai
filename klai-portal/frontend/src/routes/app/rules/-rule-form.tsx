import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

export type RuleType =
  | 'pii_block'
  | 'pii_redact'
  | 'keyword_block'
  | 'keyword_redact'

export interface RuleFormState {
  name: string
  description: string
  rule_text: string
  scope: string
  rule_type: RuleType
}

export const EMPTY_RULE_FORM: RuleFormState = {
  name: '',
  description: '',
  rule_text: '',
  scope: 'global',
  rule_type: 'pii_redact',
}

function isPiiType(type: RuleType): boolean {
  return type === 'pii_block' || type === 'pii_redact'
}

function isKeywordType(type: RuleType): boolean {
  return type === 'keyword_block' || type === 'keyword_redact'
}

interface RuleFormPageProps {
  mode: 'new' | 'edit'
  initialForm: RuleFormState
  // When editing, the slug of the existing rule.
  slug?: string
}

export function RuleFormPage({ mode, initialForm, slug }: RuleFormPageProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [form, setForm] = useState<RuleFormState>(initialForm)

  const createMutation = useMutation({
    mutationFn: async (body: RuleFormState) =>
      apiFetch('/api/app/rules', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      void navigate({ to: '/app/rules' })
    },
  })

  const updateMutation = useMutation({
    mutationFn: async (body: Partial<RuleFormState>) =>
      apiFetch(`/api/app/rules/${slug}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      void navigate({ to: '/app/rules' })
    },
  })

  const isSaving = createMutation.isPending || updateMutation.isPending
  const needsText = !isPiiType(form.rule_type)
  const isSaveDisabled =
    !form.name.trim() || (needsText && !form.rule_text.trim()) || isSaving

  const textFieldLabel = isKeywordType(form.rule_type)
    ? m.rules_field_keywords_label()
    : m.rules_field_text_label()
  const textFieldPlaceholder = isKeywordType(form.rule_type)
    ? m.rules_field_keywords_placeholder()
    : m.rules_field_text_placeholder()

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const body: RuleFormState = {
      ...form,
      rule_text: isPiiType(form.rule_type) ? '' : form.rule_text,
    }
    if (mode === 'edit') {
      updateMutation.mutate(body)
    } else {
      createMutation.mutate(body)
    }
  }

  function handleCancel() {
    void navigate({ to: '/app/rules' })
  }

  const title =
    mode === 'edit' ? m.rules_form_edit_title() : m.rules_form_new_title()
  const submitLabel = isSaving
    ? m.rules_saving()
    : mode === 'edit'
      ? m.rules_update_button()
      : m.rules_save_button()

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
          {m.rules_cancel_button()}
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="rule-type">{m.rules_type_label()}</Label>
          <Select
            id="rule-type"
            value={form.rule_type}
            onChange={(e) =>
              setForm({ ...form, rule_type: e.target.value as RuleType })
            }
          >
            <option value="pii_redact">{m.rules_type_pii_redact()}</option>
            <option value="pii_block">{m.rules_type_pii_block()}</option>
            <option value="keyword_redact">
              {m.rules_type_keyword_redact()}
            </option>
            <option value="keyword_block">
              {m.rules_type_keyword_block()}
            </option>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-name">{m.rules_field_name_label()}</Label>
          <Input
            id="rule-name"
            type="text"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder={m.rules_field_name_placeholder()}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="rule-description">
            {m.rules_field_description_label()}
          </Label>
          <Input
            id="rule-description"
            type="text"
            value={form.description}
            onChange={(e) =>
              setForm({ ...form, description: e.target.value })
            }
            placeholder={m.rules_field_description_placeholder()}
          />
        </div>

        {isPiiType(form.rule_type) ? (
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
            <p className="text-xs text-gray-600 leading-relaxed">
              {m.rules_field_pii_hint()}
            </p>
          </div>
        ) : (
          <div className="space-y-1.5">
            <Label htmlFor="rule-text">{textFieldLabel}</Label>
            <textarea
              id="rule-text"
              value={form.rule_text}
              onChange={(e) =>
                setForm({ ...form, rule_text: e.target.value })
              }
              placeholder={textFieldPlaceholder}
              rows={5}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 resize-none"
            />
            <p className="mt-1 text-xs text-gray-400">
              {form.rule_text.length}/5000
            </p>
          </div>
        )}

        <div className="space-y-1.5">
          <Label>{m.rules_field_scope_label()}</Label>
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
              {m.rules_scope_organization()}
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
              {m.rules_scope_personal()}
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
