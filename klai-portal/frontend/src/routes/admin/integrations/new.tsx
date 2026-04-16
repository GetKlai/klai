import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, Loader2, Zap, Code2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import { useCreateIntegration } from './-hooks'
import type { AccessLevel, IntegrationType } from './-types'
import { KbAccessEditor } from './_components/KbAccessEditor'
import { CreatedKeyModal } from './_components/CreatedKeyModal'

export const Route = createFileRoute('/admin/integrations/new')({
  component: NewIntegrationPage,
})

interface FormState {
  name: string
  description: string
  chat: boolean
  feedback: boolean
  knowledge_append: boolean
  rate_limit_rpm: number
  kb_access: { kb_id: number; access_level: AccessLevel }[]
}

type Step = 'type-select' | 'form'

function NewIntegrationPage() {
  const navigate = useNavigate()
  const createMutation = useCreateIntegration()

  const [step, setStep] = useState<Step>('type-select')
  const [integrationType, setIntegrationType] = useState<IntegrationType | null>(null)

  const [form, setForm] = useState<FormState>({
    name: '',
    description: '',
    chat: true,
    feedback: true,
    knowledge_append: false,
    rate_limit_rpm: 60,
    kb_access: [],
  })

  const [createdKey, setCreatedKey] = useState<string | null>(null)

  function handleTypeSelect(type: IntegrationType) {
    setIntegrationType(type)
    setStep('form')
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!integrationType) return
    createMutation.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim() || null,
        integration_type: integrationType,
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
          if (integrationType === 'api') {
            setCreatedKey(data.api_key)
          } else {
            void navigate({
              to: '/admin/integrations/$id',
              params: { id: String(data.id) },
            })
          }
        },
      },
    )
  }

  function handleKeyModalConfirm() {
    setCreatedKey(null)
    void navigate({ to: '/admin/integrations' })
  }

  // When knowledge_append is toggled off, downgrade any read_write to read
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

  if (step === 'type-select') {
    return (
      <div className="p-6 max-w-lg">
        <div className="flex items-start justify-between mb-6">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {m.admin_integrations_type_select_title()}
          </h1>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => navigate({ to: '/admin/integrations' })}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.admin_users_cancel()}
          </Button>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <button
            type="button"
            onClick={() => handleTypeSelect('api')}
            className="text-left"
          >
            <Card className="h-full border-2 border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors cursor-pointer">
              <CardContent className="pt-6">
                <div className="flex items-start gap-3">
                  <Code2 className="h-5 w-5 mt-0.5 text-[var(--color-muted-foreground)] shrink-0" />
                  <div>
                    <p className="font-medium text-sm text-[var(--color-foreground)]">
                      {m.admin_integrations_type_api_label()}
                    </p>
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
                      {m.admin_integrations_type_api_description()}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </button>

          <button
            type="button"
            onClick={() => handleTypeSelect('widget')}
            className="text-left"
          >
            <Card className="h-full border-2 border-[var(--color-border)] hover:border-[var(--color-accent)] transition-colors cursor-pointer">
              <CardContent className="pt-6">
                <div className="flex items-start gap-3">
                  <Zap className="h-5 w-5 mt-0.5 text-[var(--color-muted-foreground)] shrink-0" />
                  <div>
                    <p className="font-medium text-sm text-[var(--color-foreground)]">
                      {m.admin_integrations_type_widget_label()}
                    </p>
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
                      {m.admin_integrations_type_widget_description()}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
          {m.admin_integrations_create()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setStep('type-select')}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Basics */}
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_basics()}
          </h2>
          <div className="space-y-1.5">
            <Label htmlFor="integration-name">
              {m.admin_integrations_field_name()}
            </Label>
            <Input
              id="integration-name"
              value={form.name}
              onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              required
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="integration-description">
              {m.admin_integrations_field_description()}
            </Label>
            <textarea
              id="integration-description"
              value={form.description}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, description: e.target.value }))
              }
              rows={3}
              className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>
        </section>

        {/* Permissions — only for API integrations */}
        {integrationType === 'api' && (
          <>
            <section className="space-y-4">
              <h2 className="text-sm font-medium text-[var(--color-foreground)]">
                {m.admin_integrations_section_permissions()}
              </h2>
              <div className="space-y-4">
                <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
                  <input
                    type="checkbox"
                    checked={form.chat}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, chat: e.target.checked }))
                    }
                    className="accent-[var(--color-accent)] mt-0.5"
                  />
                  <div>
                    <span className="font-medium">{m.admin_integrations_perm_chat()}</span>
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                      {m.admin_integrations_perm_chat_description()}
                    </p>
                  </div>
                </label>
                <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
                  <input
                    type="checkbox"
                    checked={form.feedback}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, feedback: e.target.checked }))
                    }
                    className="accent-[var(--color-accent)] mt-0.5"
                  />
                  <div>
                    <span className="font-medium">{m.admin_integrations_perm_feedback()}</span>
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                      {m.admin_integrations_perm_feedback_description()}
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
                    <span className="font-medium">{m.admin_integrations_perm_knowledge_append()}</span>
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                      {m.admin_integrations_perm_knowledge_append_description()}
                    </p>
                  </div>
                </label>
              </div>
            </section>

            {/* KB Access */}
            <section className="space-y-4">
              <h2 className="text-sm font-medium text-[var(--color-foreground)]">
                {m.admin_integrations_section_kb_access()}
              </h2>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_integrations_kb_intro()}
              </p>
              <KbAccessEditor
                value={form.kb_access}
                onChange={(kb_access) => setForm((prev) => ({ ...prev, kb_access }))}
                knowledgeAppendEnabled={form.knowledge_append}
              />
            </section>

            {/* Rate limit */}
            <section className="space-y-4">
              <h2 className="text-sm font-medium text-[var(--color-foreground)]">
                {m.admin_integrations_section_rate_limit()}
              </h2>
              <div className="space-y-1.5">
                <Label htmlFor="rate-limit">
                  {m.admin_integrations_field_rate_limit()}
                </Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="rate-limit"
                    type="number"
                    min={10}
                    max={600}
                    value={form.rate_limit_rpm}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        rate_limit_rpm: Number(e.target.value),
                      }))
                    }
                    className="max-w-[8rem]"
                  />
                  <span className="text-sm text-[var(--color-muted-foreground)]">
                    {m.admin_integrations_rate_limit_unit()}
                  </span>
                </div>
              </div>
            </section>
          </>
        )}

        {createMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {createMutation.error instanceof Error
              ? createMutation.error.message
              : m.admin_integrations_error_generic()}
          </p>
        )}

        <div className="pt-2">
          <Button
            type="submit"
            disabled={createMutation.isPending || form.name.trim().length < 3}
          >
            {createMutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            {m.admin_integrations_create_submit()}
          </Button>
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
