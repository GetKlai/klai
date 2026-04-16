import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, ArrowRight, AlertTriangle, Loader2, Zap, Code2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import * as m from '@/paraglide/messages'
import { useCreateIntegration } from './-hooks'
import type { AccessLevel, IntegrationType, WidgetConfig } from './-types'
import { KbAccessEditor } from './_components/KbAccessEditor'
import { CreatedKeyModal } from './_components/CreatedKeyModal'

export const Route = createFileRoute('/admin/integrations/new')({
  component: NewIntegrationPage,
})

// Step identifiers. Widget skips the "permissions" step and adds "styling".
type ApiStep = 'type' | 'details' | 'permissions' | 'kbs' | 'settings'
type WidgetStep = 'type' | 'details' | 'kbs' | 'settings' | 'styling'
type Step = ApiStep | WidgetStep

const API_STEPS: ApiStep[] = ['type', 'details', 'permissions', 'kbs', 'settings']
const WIDGET_STEPS: WidgetStep[] = ['type', 'details', 'kbs', 'settings', 'styling']

const CSS_VAR_KEYS = [
  '--klai-primary-color',
  '--klai-text-color',
  '--klai-background-color',
  '--klai-border-radius',
] as const

type CssVarKey = (typeof CSS_VAR_KEYS)[number]

interface CssVarRow {
  key: CssVarKey | ''
  value: string
}

interface FormState {
  name: string
  description: string
  // API permissions
  chat: boolean
  feedback: boolean
  knowledge_append: boolean
  kb_access: { kb_id: number; access_level: AccessLevel }[]
  // API settings
  rate_limit_rpm: number
  // Widget setup
  allowed_origins_raw: string
  widget_title: string
  widget_welcome: string
  // Widget styling
  css_var_rows: CssVarRow[]
}

const INITIAL_FORM: FormState = {
  name: '',
  description: '',
  chat: true,
  feedback: true,
  knowledge_append: false,
  kb_access: [],
  rate_limit_rpm: 60,
  allowed_origins_raw: '',
  widget_title: '',
  widget_welcome: '',
  css_var_rows: [{ key: '', value: '' }],
}

function cssVarsToRecord(rows: CssVarRow[]): Record<string, string> {
  const result: Record<string, string> = {}
  for (const row of rows) {
    if (row.key && row.value.trim()) {
      result[row.key] = row.value.trim()
    }
  }
  return result
}

function parseOrigins(raw: string): string[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function isValidOrigin(origin: string): boolean {
  try {
    const url = new URL(origin)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}

function NewIntegrationPage() {
  const navigate = useNavigate()
  const createMutation = useCreateIntegration()

  const [integrationType, setIntegrationType] = useState<IntegrationType | null>(null)
  const [step, setStep] = useState<Step>('type')
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [createdKey, setCreatedKey] = useState<string | null>(null)

  const stepOrder: Step[] =
    integrationType === 'widget' ? WIDGET_STEPS : API_STEPS
  const currentIndex = stepOrder.indexOf(step)

  // Build step indicator labels
  const stepLabels: StepItem[] = stepOrder.map((s) => ({
    label:
      s === 'type'
        ? m.admin_integrations_wizard_step_type()
        : s === 'details'
          ? m.admin_integrations_wizard_step_details()
          : s === 'permissions'
            ? m.admin_integrations_wizard_step_permissions()
            : s === 'kbs'
              ? m.admin_integrations_wizard_step_kb_access()
              : s === 'styling'
                ? m.admin_integrations_wizard_step_styling()
                : integrationType === 'widget'
                  ? m.admin_integrations_wizard_step_setup()
                  : m.admin_integrations_wizard_step_rate_limit(),
    onClick: () => setStep(s),
  }))

  function handleTypeSelect(type: IntegrationType) {
    setIntegrationType(type)
    // Reset form when switching types so defaults match type
    setForm(INITIAL_FORM)
    setStep('details')
  }

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

  // Validate current step. Returns error message or null when valid.
  function validateStep(s: Step): string | null {
    if (s === 'type') {
      return integrationType ? null : null // button gates this step
    }
    if (s === 'details') {
      return form.name.trim().length < 3
        ? m.admin_integrations_wizard_error_name_too_short()
        : null
    }
    if (s === 'permissions') {
      return form.chat || form.feedback || form.knowledge_append
        ? null
        : m.admin_integrations_wizard_error_no_permissions()
    }
    if (s === 'kbs') {
      return form.kb_access.length === 0
        ? m.admin_integrations_wizard_error_no_kb_selected()
        : null
    }
    if (s === 'settings') {
      if (integrationType === 'widget') {
        const origins = parseOrigins(form.allowed_origins_raw)
        if (origins.length === 0) {
          return m.admin_integrations_wizard_error_no_origins()
        }
        if (origins.some((o) => !isValidOrigin(o))) {
          return m.admin_integrations_wizard_error_invalid_origins()
        }
        return null
      }
      return null
    }
    return null
  }

  const currentStepError = validateStep(step)

  function handleNext() {
    if (currentStepError) return
    const nextIndex = currentIndex + 1
    if (nextIndex < stepOrder.length) {
      setStep(stepOrder[nextIndex])
    }
  }

  function handlePrevious() {
    const prevIndex = currentIndex - 1
    if (prevIndex >= 0) {
      setStep(stepOrder[prevIndex])
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!integrationType) return

    // Final validation of the current step before submit
    if (currentStepError) return

    const widgetConfig: WidgetConfig | undefined =
      integrationType === 'widget'
        ? {
            allowed_origins: parseOrigins(form.allowed_origins_raw),
            title: form.widget_title.trim(),
            welcome_message: form.widget_welcome.trim(),
            css_variables: cssVarsToRecord(form.css_var_rows),
          }
        : undefined

    createMutation.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim() || null,
        integration_type: integrationType,
        permissions:
          integrationType === 'widget'
            ? { chat: true, feedback: false, knowledge_append: false }
            : {
                chat: form.chat,
                feedback: form.feedback,
                knowledge_append: form.knowledge_append,
              },
        rate_limit_rpm: form.rate_limit_rpm,
        kb_access: form.kb_access,
        widget_config: widgetConfig,
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

  const isLastStep = currentIndex === stepOrder.length - 1

  return (
    <div className="p-6 max-w-2xl">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
          {m.admin_integrations_create()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/integrations' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_integrations_wizard_cancel()}
        </Button>
      </div>

      {/* Step indicator — only shown after type is chosen */}
      {integrationType && (
        <div className="mb-8">
          <StepIndicator steps={stepLabels} currentIndex={currentIndex} />
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Step 1: Type select */}
        {step === 'type' && (
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
        )}

        {/* Step 2: Details */}
        {step === 'details' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_integrations_wizard_details_intro()}
            </p>
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
        )}

        {/* Step 3 (API only): Permissions */}
        {step === 'permissions' && (
          <section className="space-y-4">
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
        )}

        {/* Step 3/4: Knowledge bases */}
        {step === 'kbs' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {integrationType === 'widget'
                ? m.admin_integrations_wizard_kb_access_intro_widget()
                : m.admin_integrations_wizard_kb_access_intro_api()}
            </p>
            <KbAccessEditor
              value={form.kb_access}
              onChange={(kb_access) => setForm((prev) => ({ ...prev, kb_access }))}
              knowledgeAppendEnabled={form.knowledge_append}
              hideReadWrite={integrationType === 'widget'}
            />
          </section>
        )}

        {/* Step 4/5: Settings (rate limit for API, appearance for widget) */}
        {step === 'settings' && integrationType === 'api' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_integrations_wizard_rate_limit_intro()}
            </p>
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
        )}

        {step === 'settings' && integrationType === 'widget' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_integrations_wizard_setup_intro()}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="widget-title">
                {m.admin_integrations_widget_title_label()}
              </Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_integrations_widget_title_help()}
              </p>
              <Input
                id="widget-title"
                value={form.widget_title}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, widget_title: e.target.value }))
                }
                placeholder={m.admin_integrations_widget_title_placeholder()}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="widget-welcome">
                {m.admin_integrations_widget_welcome_label()}
              </Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_integrations_widget_welcome_help()}
              </p>
              <Input
                id="widget-welcome"
                value={form.widget_welcome}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, widget_welcome: e.target.value }))
                }
                placeholder={m.admin_integrations_widget_welcome_placeholder()}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="widget-origins">
                {m.admin_integrations_widget_origins_label()}
              </Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_integrations_widget_origins_help()}
              </p>
              <textarea
                id="widget-origins"
                value={form.allowed_origins_raw}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, allowed_origins_raw: e.target.value }))
                }
                rows={4}
                placeholder={m.admin_integrations_widget_origins_placeholder()}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm font-mono text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
              />
              {parseOrigins(form.allowed_origins_raw).length === 0 && (
                <div className="flex items-start gap-1.5 text-xs text-[var(--color-muted-foreground)]">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px text-[var(--color-destructive)]" />
                  {m.admin_integrations_widget_origins_empty_warning()}
                </div>
              )}
            </div>
          </section>
        )}

        {/* Step 5 (widget only): Styling + embed preview */}
        {step === 'styling' && integrationType === 'widget' && (
          <section className="space-y-6">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_integrations_wizard_styling_intro()}
            </p>

            {/* CSS variables editor */}
            <div className="space-y-2">
              <Label>{m.admin_integrations_widget_css_vars_label()}</Label>
              <div className="space-y-2">
                {form.css_var_rows.map((row, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <select
                      value={row.key}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          css_var_rows: prev.css_var_rows.map((r, i) =>
                            i === index
                              ? { ...r, key: e.target.value as CssVarKey | '' }
                              : r,
                          ),
                        }))
                      }
                      className="flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-xs font-mono text-[var(--color-foreground)] outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
                    >
                      <option value="">
                        {m.admin_integrations_widget_css_var_placeholder()}
                      </option>
                      {CSS_VAR_KEYS.map((k) => (
                        <option key={k} value={k}>
                          {k}
                        </option>
                      ))}
                    </select>
                    <Input
                      value={row.value}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          css_var_rows: prev.css_var_rows.map((r, i) =>
                            i === index ? { ...r, value: e.target.value } : r,
                          ),
                        }))
                      }
                      placeholder="#000000"
                      className="flex-1 text-xs font-mono"
                    />
                    <button
                      type="button"
                      onClick={() =>
                        setForm((prev) => ({
                          ...prev,
                          css_var_rows: prev.css_var_rows.filter((_, i) => i !== index),
                        }))
                      }
                      className="text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
                      aria-label={m.admin_integrations_widget_css_var_remove()}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              {form.css_var_rows.length < CSS_VAR_KEYS.length && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setForm((prev) => ({
                      ...prev,
                      css_var_rows: [...prev.css_var_rows, { key: '', value: '' }],
                    }))
                  }
                  className="text-xs"
                >
                  {m.admin_integrations_widget_css_var_add()}
                </Button>
              )}
            </div>

            {/* Embed snippet preview */}
            <div className="space-y-2 pt-4 border-t border-[var(--color-border)]">
              <Label>{m.admin_integrations_wizard_embed_preview_label()}</Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_integrations_wizard_embed_preview_help()}
              </p>
              <pre className="text-xs font-mono text-[var(--color-foreground)] bg-[var(--color-muted)] border border-[var(--color-border)] rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
{`<script
  src="https://cdn.getklai.com/widget/klai-chat.js"
  data-widget-id="wgt_xxxxxxxxxxxxxxxxxxxx"
></script>`}
              </pre>
            </div>
          </section>
        )}

        {/* Step-level error */}
        {currentStepError && step !== 'type' && (
          <p className="text-sm text-[var(--color-destructive)]">
            {currentStepError}
          </p>
        )}

        {/* Mutation error */}
        {createMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {createMutation.error instanceof Error
              ? createMutation.error.message
              : m.admin_integrations_error_generic()}
          </p>
        )}

        {/* Navigation */}
        {step !== 'type' && (
          <div className="flex items-center justify-between pt-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handlePrevious}
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              {m.admin_integrations_wizard_previous()}
            </Button>
            {isLastStep ? (
              <Button
                type="submit"
                disabled={createMutation.isPending || !!currentStepError}
              >
                {createMutation.isPending && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                {m.admin_integrations_wizard_create()}
              </Button>
            ) : (
              <Button
                type="button"
                onClick={handleNext}
                disabled={!!currentStepError}
              >
                {m.admin_integrations_wizard_next()}
                <ArrowRight className="h-4 w-4 ml-2" />
              </Button>
            )}
          </div>
        )}
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
