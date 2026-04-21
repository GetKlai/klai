import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, ArrowRight, AlertTriangle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import * as m from '@/paraglide/messages'
import { useCreateWidget } from './-hooks'
import type { WidgetConfig } from './-types'
import { KbAccessEditor } from './_components/KbAccessEditor'

export const Route = createFileRoute('/admin/widgets/new')({
  component: NewWidgetPage,
})

type Step = 'details' | 'kbs' | 'appearance' | 'embed'
const STEPS: Step[] = ['details', 'kbs', 'appearance', 'embed']

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
  kb_ids: number[]
  rate_limit_rpm: number
  widget_title: string
  widget_welcome: string
  allowed_origins_raw: string
  css_var_rows: CssVarRow[]
}

const INITIAL_FORM: FormState = {
  name: '',
  description: '',
  kb_ids: [],
  rate_limit_rpm: 60,
  widget_title: '',
  widget_welcome: '',
  allowed_origins_raw: '',
  css_var_rows: [{ key: '', value: '' }],
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

function cssVarsToRecord(rows: CssVarRow[]): Record<string, string> {
  const result: Record<string, string> = {}
  for (const row of rows) {
    if (row.key && row.value.trim()) {
      result[row.key] = row.value.trim()
    }
  }
  return result
}

function NewWidgetPage() {
  const navigate = useNavigate()
  const createMutation = useCreateWidget()
  const [step, setStep] = useState<Step>('details')
  const [form, setForm] = useState<FormState>(INITIAL_FORM)

  const currentIndex = STEPS.indexOf(step)

  const stepLabels: StepItem[] = STEPS.map((s) => ({
    label:
      s === 'details'
        ? m.admin_shared_wizard_step_details()
        : s === 'kbs'
          ? m.admin_shared_wizard_step_kb_access()
          : s === 'appearance'
            ? m.admin_widgets_wizard_step_appearance()
            : m.admin_widgets_wizard_step_embed(),
    onClick: () => setStep(s),
  }))

  function validateStep(s: Step): string | null {
    if (s === 'details') {
      return form.name.trim().length < 3
        ? m.admin_shared_wizard_error_name_too_short()
        : null
    }
    if (s === 'kbs') {
      return form.kb_ids.length === 0
        ? m.admin_shared_wizard_error_no_kb_selected()
        : null
    }
    if (s === 'embed') {
      const origins = parseOrigins(form.allowed_origins_raw)
      if (origins.length === 0) return m.admin_widgets_wizard_error_no_origins()
      if (origins.some((o) => !isValidOrigin(o)))
        return m.admin_widgets_wizard_error_invalid_origins()
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

    const widgetConfig: WidgetConfig = {
      allowed_origins: parseOrigins(form.allowed_origins_raw),
      title: form.widget_title.trim(),
      welcome_message: form.widget_welcome.trim(),
      css_variables: cssVarsToRecord(form.css_var_rows),
    }

    createMutation.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim() || null,
        kb_ids: form.kb_ids,
        rate_limit_rpm: form.rate_limit_rpm,
        widget_config: widgetConfig,
      },
      {
        onSuccess: (data) => {
          void navigate({
            to: '/admin/widgets/$id',
            params: { id: String(data.id) },
          })
        },
      },
    )
  }

  const isLastStep = currentIndex === STEPS.length - 1

  return (
    <div className="p-6 max-w-2xl">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
          {m.admin_widgets_create()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/widgets' })}
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
              <Label htmlFor="widget-name">
                {m.admin_shared_field_name()}
              </Label>
              <Input
                id="widget-name"
                value={form.name}
                onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                required
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="widget-description">
                {m.admin_shared_field_description()}
              </Label>
              <textarea
                id="widget-description"
                value={form.description}
                onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))}
                rows={3}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>
          </section>
        )}

        {step === 'kbs' && (
          <section className="space-y-4">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_widgets_wizard_kb_access_intro_widget()}
            </p>
            <KbAccessEditor
              value={form.kb_ids}
              onChange={(kb_ids) => setForm((p) => ({ ...p, kb_ids }))}
            />
          </section>
        )}

        {step === 'appearance' && (
          <section className="space-y-6">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_widgets_wizard_appearance_intro()}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="widget-title">{m.admin_widgets_widget_title_label()}</Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_widgets_widget_title_help()}
              </p>
              <Input
                id="widget-title"
                value={form.widget_title}
                onChange={(e) => setForm((p) => ({ ...p, widget_title: e.target.value }))}
                placeholder={m.admin_widgets_widget_title_placeholder()}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="widget-welcome">{m.admin_widgets_widget_welcome_label()}</Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_widgets_widget_welcome_help()}
              </p>
              <Input
                id="widget-welcome"
                value={form.widget_welcome}
                onChange={(e) => setForm((p) => ({ ...p, widget_welcome: e.target.value }))}
                placeholder={m.admin_widgets_widget_welcome_placeholder()}
              />
            </div>
            <div className="space-y-2">
              <Label>{m.admin_widgets_widget_css_vars_label()}</Label>
              <div className="space-y-2">
                {form.css_var_rows.map((row, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <select
                      value={row.key}
                      onChange={(e) =>
                        setForm((p) => ({
                          ...p,
                          css_var_rows: p.css_var_rows.map((r, i) =>
                            i === index
                              ? { ...r, key: e.target.value as CssVarKey | '' }
                              : r,
                          ),
                        }))
                      }
                      className="flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-xs font-mono text-[var(--color-foreground)] outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
                    >
                      <option value="">{m.admin_widgets_widget_css_var_placeholder()}</option>
                      {CSS_VAR_KEYS.map((k) => (
                        <option key={k} value={k}>
                          {k}
                        </option>
                      ))}
                    </select>
                    <Input
                      value={row.value}
                      onChange={(e) =>
                        setForm((p) => ({
                          ...p,
                          css_var_rows: p.css_var_rows.map((r, i) =>
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
                        setForm((p) => ({
                          ...p,
                          css_var_rows: p.css_var_rows.filter((_, i) => i !== index),
                        }))
                      }
                      className="text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
                      aria-label={m.admin_widgets_widget_css_var_remove()}
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
                    setForm((p) => ({
                      ...p,
                      css_var_rows: [...p.css_var_rows, { key: '', value: '' }],
                    }))
                  }
                  className="text-xs"
                >
                  {m.admin_widgets_widget_css_var_add()}
                </Button>
              )}
            </div>
          </section>
        )}

        {step === 'embed' && (
          <section className="space-y-6">
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_widgets_wizard_embed_intro()}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="widget-origins">
                {m.admin_widgets_widget_origins_label()}
              </Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_widgets_widget_origins_help()}
              </p>
              <textarea
                id="widget-origins"
                value={form.allowed_origins_raw}
                onChange={(e) => setForm((p) => ({ ...p, allowed_origins_raw: e.target.value }))}
                rows={4}
                placeholder={m.admin_widgets_widget_origins_placeholder()}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm font-mono text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)]"
              />
              {parseOrigins(form.allowed_origins_raw).length === 0 && (
                <div className="flex items-start gap-1.5 text-xs text-[var(--color-muted-foreground)]">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px text-[var(--color-destructive)]" />
                  {m.admin_widgets_widget_origins_empty_warning()}
                </div>
              )}
            </div>
            <div className="space-y-2 pt-4 border-t border-[var(--color-border)]">
              <Label>{m.admin_widgets_wizard_embed_preview_label()}</Label>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {m.admin_widgets_wizard_embed_preview_help()}
              </p>
              <pre className="text-xs font-mono text-[var(--color-foreground)] bg-[var(--color-muted)] border border-[var(--color-border)] rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
{`<script
  src="https://my.getklai.com/widget/klai-chat.js"
  data-widget-id="wgt_xxxxxxxxxxxxxxxxxxxx"
></script>`}
              </pre>
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
    </div>
  )
}
