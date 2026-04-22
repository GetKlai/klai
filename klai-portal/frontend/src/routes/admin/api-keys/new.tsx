import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, ArrowRight, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import * as m from '@/paraglide/messages'
import { useCreateApiKey } from './-hooks'
import type { AccessLevel } from './-types'
import { KbAccessEditor } from './_components/KbAccessEditor'
import { CreatedKeyModal } from './_components/CreatedKeyModal'

export const Route = createFileRoute('/admin/api-keys/new')({
  component: NewApiKeyPage,
})

type Step = 'details' | 'permissions' | 'kbs' | 'rate_limit'
const STEPS: Step[] = ['details', 'permissions', 'kbs', 'rate_limit']

interface FormState {
  name: string
  description: string
  chat: boolean
  feedback: boolean
  knowledge_append: boolean
  kb_access: { kb_id: number; access_level: AccessLevel }[]
  rate_limit_rpm: number
}

const INITIAL_FORM: FormState = {
  name: '',
  description: '',
  chat: true,
  feedback: true,
  knowledge_append: false,
  kb_access: [],
  rate_limit_rpm: 60,
}

function NewApiKeyPage() {
  const navigate = useNavigate()
  const createMutation = useCreateApiKey()
  const [step, setStep] = useState<Step>('details')
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [createdKey, setCreatedKey] = useState<string | null>(null)

  const currentIndex = STEPS.indexOf(step)

  const stepLabels: StepItem[] = STEPS.map((s) => ({
    label:
      s === 'details'
        ? m.admin_shared_wizard_step_details()
        : s === 'permissions'
          ? m.admin_api_keys_wizard_step_permissions()
          : s === 'kbs'
            ? m.admin_shared_wizard_step_kb_access()
            : m.admin_api_keys_wizard_step_rate_limit(),
    onClick: () => setStep(s),
  }))

  function handleKnowledgeAppendChange(checked: boolean) {
    setForm((prev) => ({
      ...prev,
      knowledge_append: checked,
      kb_access: checked
        ? prev.kb_access
        : prev.kb_access.map((row) =>
            row.access_level === 'read_write'
              ? { ...row, access_level: 'read' as AccessLevel }
              : row,
          ),
    }))
  }

  function validateStep(s: Step): string | null {
    if (s === 'details') {
      return form.name.trim().length < 3
        ? m.admin_shared_wizard_error_name_too_short()
        : null
    }
    if (s === 'permissions') {
      return form.chat || form.feedback || form.knowledge_append
        ? null
        : m.admin_api_keys_wizard_error_no_permissions()
    }
    if (s === 'kbs') {
      return form.kb_access.length === 0
        ? m.admin_shared_wizard_error_no_kb_selected()
        : null
    }
    return null
  }

  const currentStepError = validateStep(step)

  function handleNext() {
    if (currentStepError) return
    const next = currentIndex + 1
    if (next < STEPS.length) setStep(STEPS[next])
  }

  function handlePrevious() {
    const prev = currentIndex - 1
    if (prev >= 0) setStep(STEPS[prev])
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (currentStepError) return

    createMutation.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim() || null,
        permissions: {
          chat: form.chat,
          feedback: form.feedback,
          knowledge_append: form.knowledge_append,
        },
        rate_limit_rpm: form.rate_limit_rpm,
        kb_access: form.kb_access,
      },
      {
        onSuccess: (data) => {
          setCreatedKey(data.api_key)
        },
      },
    )
  }

  function handleKeyModalConfirm() {
    setCreatedKey(null)
    void navigate({ to: '/admin/api-keys' })
  }

  const isLastStep = currentIndex === STEPS.length - 1

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-[26px] font-display-bold text-gray-900">
          {m.admin_api_keys_create()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/api-keys' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_shared_wizard_cancel()}
        </Button>
      </div>

      <div className="mb-8">
        <StepIndicator steps={stepLabels} currentIndex={currentIndex} />
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {step === 'details' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_shared_wizard_details_intro()}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="api-key-name">
                {m.admin_shared_field_name()}
              </Label>
              <Input
                id="api-key-name"
                value={form.name}
                onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                required
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="api-key-description">
                {m.admin_shared_field_description()}
              </Label>
              <textarea
                id="api-key-description"
                value={form.description}
                onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))}
                rows={3}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>
          </section>
        )}

        {step === 'permissions' && (
          <section className="space-y-4">
            <div className="space-y-4">
              <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
                <input
                  type="checkbox"
                  checked={form.chat}
                  onChange={(e) => setForm((p) => ({ ...p, chat: e.target.checked }))}
                  className="accent-[var(--color-accent)] mt-0.5"
                />
                <div>
                  <span className="font-medium">{m.admin_api_keys_perm_chat()}</span>
                  <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                    {m.admin_api_keys_perm_chat_description()}
                  </p>
                </div>
              </label>
              <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
                <input
                  type="checkbox"
                  checked={form.feedback}
                  onChange={(e) => setForm((p) => ({ ...p, feedback: e.target.checked }))}
                  className="accent-[var(--color-accent)] mt-0.5"
                />
                <div>
                  <span className="font-medium">{m.admin_api_keys_perm_feedback()}</span>
                  <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                    {m.admin_api_keys_perm_feedback_description()}
                  </p>
                </div>
              </label>
              <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
                <input
                  type="checkbox"
                  checked={form.knowledge_append}
                  onChange={(e) => handleKnowledgeAppendChange(e.target.checked)}
                  className="accent-[var(--color-accent)] mt-0.5"
                />
                <div>
                  <span className="font-medium">{m.admin_api_keys_perm_knowledge_append()}</span>
                  <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                    {m.admin_api_keys_perm_knowledge_append_description()}
                  </p>
                </div>
              </label>
            </div>
          </section>
        )}

        {step === 'kbs' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_api_keys_wizard_kb_access_intro_api()}
            </p>
            <KbAccessEditor
              value={form.kb_access}
              onChange={(kb_access) => setForm((p) => ({ ...p, kb_access }))}
              knowledgeAppendEnabled={form.knowledge_append}
            />
          </section>
        )}

        {step === 'rate_limit' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_api_keys_wizard_rate_limit_intro()}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="rate-limit">
                {m.admin_api_keys_field_rate_limit()}
              </Label>
              <div className="flex items-center gap-2">
                <Input
                  id="rate-limit"
                  type="number"
                  min={10}
                  max={600}
                  value={form.rate_limit_rpm}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, rate_limit_rpm: Number(e.target.value) }))
                  }
                  className="max-w-[8rem]"
                />
                <span className="text-sm text-[var(--color-muted-foreground)]">
                  {m.admin_api_keys_rate_limit_unit()}
                </span>
              </div>
            </div>
          </section>
        )}

        {currentStepError && (
          <p className="text-sm text-[var(--color-destructive)]">{currentStepError}</p>
        )}

        {createMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {createMutation.error instanceof Error
              ? createMutation.error.message
              : m.admin_shared_error_generic()}
          </p>
        )}

        <div className="flex items-center justify-between pt-2">
          <Button type="button" variant="ghost" size="sm" onClick={handlePrevious} disabled={currentIndex === 0}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.admin_shared_wizard_previous()}
          </Button>
          {isLastStep ? (
            <Button type="submit" disabled={createMutation.isPending || !!currentStepError}>
              {createMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {m.admin_shared_wizard_create()}
            </Button>
          ) : (
            <Button type="button" onClick={handleNext} disabled={!!currentStepError}>
              {m.admin_shared_wizard_next()}
              <ArrowRight className="h-4 w-4 ml-2" />
            </Button>
          )}
        </div>
      </form>

      {createdKey && (
        <CreatedKeyModal
          apiKey={createdKey}
          open={!!createdKey}
          onConfirm={handleKeyModalConfirm}
        />
      )}
    </div>
  )
}
