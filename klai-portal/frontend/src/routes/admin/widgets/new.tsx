import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, ArrowRight, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import { Textarea } from '@/components/ui/textarea'
import { WidgetToggleCard } from '@/features/widgets/components/WidgetToggleCard'
import { isValidOrigin, parseOrigins } from '@/features/widgets/config/origins'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { useCreateWidget } from './-hooks'
import type { WidgetConfig } from './-types'
import { KbAccessEditor } from './_components/KbAccessEditor'

// Create flow walks through the SAME sections the edit page exposes
// (Algemeen / Kennisbanken / Vormgeving / Insluiten) so the admin
// can configure every TWD field in one go. Each step mirrors the
// corresponding tab in /admin/widgets/$id; after the last step we
// POST once and land the admin on the detail page where the same
// fields are available for further refinement.

export const Route = createFileRoute('/admin/widgets/new')({
  component: NewWidgetPage,
})

interface Template {
  slug: string
  name: string
  prompt_text: string
}

type Step = 'details' | 'kbs' | 'appearance' | 'embed'
const STEPS: Step[] = ['details', 'kbs', 'appearance', 'embed']
const MAX_STARTERS = 6

interface FormState {
  // Algemeen
  name: string
  description: string
  template_slug: string
  system_prompt: string
  // Kennisbanken
  kb_ids: number[]
  // Vormgeving (widget titel = bot naam; geen apart veld)
  primary_color: string
  theme: 'light' | 'dark'
  welcome_message: string
  starters_raw: string
  show_sources: boolean
  show_meta: boolean
  collect_user_info: boolean
  hide_disclaimer: boolean
  widget_position: 'left' | 'right'
  public_share_enabled: boolean
  // Insluiten
  allowed_origins_raw: string
  allow_any_origin: boolean
}

const INITIAL_FORM: FormState = {
  name: '',
  description: '',
  template_slug: '',
  system_prompt: '',
  kb_ids: [],
  primary_color: '#fcaa2d',
  theme: 'light',
  welcome_message: '',
  starters_raw: '',
  show_sources: true,
  show_meta: false,
  collect_user_info: false,
  hide_disclaimer: false,
  widget_position: 'right',
  public_share_enabled: false,
  allowed_origins_raw: '',
  allow_any_origin: false,
}

function NewWidgetPage() {
  const navigate = useNavigate()
  const createMutation = useCreateWidget()
  const [step, setStep] = useState<Step>('details')
  const [form, setForm] = useState<FormState>(INITIAL_FORM)

  const templatesQuery = useQuery<Template[]>({
    queryKey: ['app-templates'],
    queryFn: () => apiFetch<Template[]>('/api/app/templates'),
  })

  const currentIndex = STEPS.indexOf(step)
  const isLastStep = currentIndex === STEPS.length - 1
  const starters = form.starters_raw
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(0, MAX_STARTERS)

  const stepLabels: StepItem[] = [
    { label: m.admin_shared_wizard_step_details(), onClick: () => setStep('details') },
    { label: m.admin_shared_wizard_step_kb_access(), onClick: () => setStep('kbs') },
    { label: m.admin_widgets_wizard_step_appearance(), onClick: () => setStep('appearance') },
    { label: m.admin_widgets_wizard_step_embed(), onClick: () => setStep('embed') },
  ]

  function validateStep(s: Step): string | null {
    if (s === 'details') {
      if (form.name.trim().length < 3)
        return m.admin_shared_wizard_error_name_too_short()
    }
    if (s === 'kbs') {
      if (form.kb_ids.length === 0)
        return m.admin_shared_wizard_error_no_kb_selected()
    }
    if (s === 'embed') {
      // Origins are optional - empty list = widget loads anywhere.
      // Only block if something was typed AND it doesn't parse.
      const origins = parseOrigins(form.allowed_origins_raw)
      if (origins.length > 0 && origins.some((o) => !isValidOrigin(o)))
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

  function fillFromTemplate(slug: string) {
    setForm((p) => {
      const t = templatesQuery.data?.find((x) => x.slug === slug)
      return {
        ...p,
        template_slug: slug,
        system_prompt:
          t && !p.system_prompt.trim() ? t.prompt_text : p.system_prompt,
      }
    })
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    // Pressing Enter inside any input inside the form fires onSubmit
    // even when the focused button is type="button". Guard: never
    // create the widget until the admin is actually on the Insluiten
    // step - earlier steps advance instead.
    if (!isLastStep) {
      handleNext()
      return
    }
    if (currentStepError) return

    const widgetConfig: WidgetConfig = {
      allowed_origins: parseOrigins(form.allowed_origins_raw),
      title: form.name.trim(),
      welcome_message: form.welcome_message.trim(),
      system_prompt: form.system_prompt.trim(),
      css_variables: {},
      conversation_starters: starters,
      hide_disclaimer: form.hide_disclaimer,
      template_slug: form.template_slug || null,
      primary_color: form.primary_color,
      theme: form.theme,
      show_sources: form.show_sources,
      show_meta: form.show_meta,
      collect_user_info: form.collect_user_info,
      widget_position: form.widget_position,
    }

    createMutation.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim() || null,
        kb_ids: form.kb_ids,
        rate_limit_rpm: 60,
        widget_config: widgetConfig,
        public_share_enabled: form.public_share_enabled,
        allow_any_origin: form.allow_any_origin,
      },
      {
        onSuccess: (data) => {
          // Land on the Insluiten tab so the admin immediately sees
          // the share link, embed snippet, and Test button - the
          // whole point of running the wizard was to ship the widget.
          void navigate({
            to: '/admin/widgets/$id',
            params: { id: String(data.id) },
            search: { tab: 'embed' },
          })
        },
      },
    )
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
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
          <section className="space-y-8">
            {/* Basis Informatie */}
            <div>
              <SectionHeading>
                {m.admin_widgets_details_section_basics()}
              </SectionHeading>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="widget-name">
                    {m.admin_shared_field_name()}
                  </Label>
                  <Input
                    id="widget-name"
                    value={form.name}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, name: e.target.value }))
                    }
                    placeholder={m.admin_widgets_name_placeholder()}
                    required
                    autoFocus
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="widget-description">
                    {m.admin_widgets_details_role_scope_label()}
                  </Label>
                  <p className="text-xs text-gray-400">
                    {m.admin_widgets_details_role_scope_help()}
                  </p>
                  <Textarea
                    id="widget-description"
                    value={form.description}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, description: e.target.value }))
                    }
                    rows={3}
                    placeholder={m.admin_widgets_role_scope_placeholder()}
                  />
                </div>
              </div>
            </div>

            {/* AI Configuratie */}
            <div className="border-t border-gray-200 pt-6">
              <SectionHeading>
                {m.admin_widgets_details_section_ai()}
              </SectionHeading>
              <div className="space-y-4">
                {templatesQuery.data && templatesQuery.data.length > 0 && (
                  <div className="space-y-1.5">
                    <Label htmlFor="widget-template">
                      {m.admin_widgets_widget_template_label()}
                    </Label>
                    <p className="text-xs text-gray-400">
                      {m.admin_widgets_widget_template_help()}
                    </p>
                    <Select
                      id="widget-template"
                      value={form.template_slug}
                      onChange={(e) => fillFromTemplate(e.target.value)}
                      className="max-w-md"
                    >
                      <option value="">
                        {m.admin_widgets_widget_template_none()}
                      </option>
                      {templatesQuery.data.map((t) => (
                        <option key={t.slug} value={t.slug}>
                          {t.name}
                        </option>
                      ))}
                    </Select>
                  </div>
                )}
                <div className="space-y-1.5">
                  <Label htmlFor="widget-system-prompt">
                    {m.admin_widgets_widget_system_prompt_label()}
                  </Label>
                  <p className="text-xs text-gray-400">
                    {m.admin_widgets_widget_system_prompt_help()}
                  </p>
                  <Textarea
                    id="widget-system-prompt"
                    value={form.system_prompt}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, system_prompt: e.target.value }))
                    }
                    rows={6}
                    maxLength={4000}
                    placeholder={m.admin_widgets_widget_system_prompt_placeholder()}
                  />
                </div>
              </div>
            </div>
          </section>
        )}

        {step === 'kbs' && (
          <section className="space-y-4">
            <p className="text-sm text-gray-400">
              {m.admin_widgets_wizard_kb_access_intro_widget()}
            </p>
            <KbAccessEditor
              value={form.kb_ids}
              onChange={(kb_ids) => setForm((p) => ({ ...p, kb_ids }))}
            />
          </section>
        )}

        {step === 'appearance' && (
          <section className="space-y-8">
            {/* Brand & Theme */}
            <div>
              <SectionHeading>
                {m.admin_widgets_appearance_section_brand()}
              </SectionHeading>
              <div className="space-y-1.5 max-w-sm">
                <Label htmlFor="widget-primary-color">
                  {m.admin_widgets_brand_color_label()}
                </Label>
                <p className="text-xs text-gray-400">
                  {m.admin_widgets_brand_color_help()}
                </p>
                <div className="flex items-center gap-2">
                  <Input
                    id="widget-primary-color"
                    type="color"
                    value={form.primary_color}
                    onChange={(e) =>
                      setForm((p) => ({
                        ...p,
                        primary_color: e.target.value,
                      }))
                    }
                    className="h-10 w-12 cursor-pointer rounded-md border border-gray-200 p-1"
                  />
                  <Input
                    value={form.primary_color}
                    onChange={(e) =>
                      setForm((p) => ({
                        ...p,
                        primary_color: e.target.value,
                      }))
                    }
                    pattern="^#[0-9a-fA-F]{6}$"
                    placeholder={m.admin_widgets_brand_color_placeholder()}
                    className="font-mono text-sm"
                  />
                </div>
              </div>

              <div className="mt-5 space-y-1.5">
                <Label>{m.admin_widgets_theme_label()}</Label>
                <div
                  role="radiogroup"
                  className="inline-flex items-center gap-0.5 rounded-full border border-gray-200 p-0.5"
                >
                  {(['light', 'dark'] as const).map((t) => (
                    <Button
                      key={t}
                      type="button"
                      onClick={() => setForm((p) => ({ ...p, theme: t }))}
                      role="radio"
                      aria-checked={form.theme === t}
                      variant="ghost"
                      size="sm"
                      className={
                        form.theme === t
                          ? 'rounded-full bg-gray-900 px-4 py-1.5 text-[12px] font-medium text-white transition-colors'
                          : 'rounded-full px-4 py-1.5 text-[12px] text-gray-500 hover:text-gray-900 klai-hover'
                      }
                    >
                      {t === 'light'
                        ? m.admin_widgets_theme_light()
                        : m.admin_widgets_theme_dark()}
                    </Button>
                  ))}
                </div>
              </div>
            </div>

            {/* Welkomstbericht */}
            <div className="border-t border-gray-200 pt-6">
              <SectionHeading>
                {m.admin_widgets_appearance_section_welcome()}
              </SectionHeading>
              <div className="space-y-1.5">
                <Label htmlFor="widget-welcome">
                  {m.admin_widgets_welcome_label()}
                </Label>
                <p className="text-xs text-gray-400">
                  {m.admin_widgets_welcome_help()}
                </p>
                <Input
                  id="widget-welcome"
                  value={form.welcome_message}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, welcome_message: e.target.value }))
                  }
                  placeholder={m.admin_widgets_widget_welcome_placeholder()}
                />
              </div>
            </div>

            {/* Conversatie starters */}
            <div className="border-t border-gray-200 pt-6">
              <SectionHeading>
                {m.admin_widgets_appearance_section_starters()}
              </SectionHeading>
              <div className="space-y-1.5">
                <Label htmlFor="widget-starters">
                  {m.admin_widgets_widget_starters_label()}
                </Label>
                <p className="text-xs text-gray-400">
                  {m.admin_widgets_widget_starters_help()}
                </p>
                <Textarea
                  id="widget-starters"
                  value={form.starters_raw}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, starters_raw: e.target.value }))
                  }
                  rows={4}
                  placeholder={m.admin_widgets_widget_starters_placeholder()}
                />
                <p className="text-xs text-gray-400">
                  {starters.length}/{MAX_STARTERS}
                </p>
              </div>
            </div>

            {/* Chat Weergave */}
            <div className="border-t border-gray-200 pt-6">
              <SectionHeading>
                {m.admin_widgets_appearance_section_chat_display()}
              </SectionHeading>
              <div className="space-y-3">
                <WidgetToggleCard
                  id="show-sources"
                  checked={form.show_sources}
                  onChange={(v) => setForm((p) => ({ ...p, show_sources: v }))}
                  label={m.admin_widgets_show_sources_label()}
                  help={m.admin_widgets_show_sources_help()}
                />
                <WidgetToggleCard
                  id="show-meta"
                  checked={form.show_meta}
                  onChange={(v) => setForm((p) => ({ ...p, show_meta: v }))}
                  label={m.admin_widgets_show_meta_label()}
                  help={m.admin_widgets_show_meta_help()}
                />
                <WidgetToggleCard
                  id="collect-user-info"
                  checked={form.collect_user_info}
                  onChange={(v) =>
                    setForm((p) => ({ ...p, collect_user_info: v }))
                  }
                  label={m.admin_widgets_collect_user_info_label()}
                  help={m.admin_widgets_collect_user_info_help()}
                />
                <WidgetToggleCard
                  id="hide-disclaimer"
                  checked={form.hide_disclaimer}
                  onChange={(v) =>
                    setForm((p) => ({ ...p, hide_disclaimer: v }))
                  }
                  label={m.admin_widgets_widget_hide_disclaimer_label()}
                  help={m.admin_widgets_widget_hide_disclaimer_help()}
                />
              </div>
            </div>

            {/* Widget positie */}
            <div className="border-t border-gray-200 pt-6">
              <SectionHeading>
                {m.admin_widgets_appearance_section_position()}
              </SectionHeading>
              <div
                role="radiogroup"
                className="inline-flex items-center gap-0.5 rounded-full border border-gray-200 p-0.5"
              >
                {(['left', 'right'] as const).map((pos) => (
                  <Button
                    key={pos}
                    type="button"
                    onClick={() =>
                      setForm((p) => ({ ...p, widget_position: pos }))
                    }
                    role="radio"
                    aria-checked={form.widget_position === pos}
                    variant="ghost"
                    size="sm"
                    className={
                      form.widget_position === pos
                        ? 'rounded-full bg-gray-900 px-4 py-1.5 text-[12px] font-medium text-white transition-colors'
                        : 'rounded-full px-4 py-1.5 text-[12px] text-gray-500 hover:text-gray-900 klai-hover'
                    }
                  >
                    {pos === 'left'
                      ? m.admin_widgets_position_left()
                      : m.admin_widgets_position_right()}
                  </Button>
                ))}
              </div>
            </div>
          </section>
        )}

        {step === 'embed' && (
          <section className="space-y-4">
            <p className="text-sm text-gray-400">
              Standaard werkt je widget overal. Wil je hem alleen op
              specifieke domeinen laten laden? Vul ze hieronder in - één
              per regel. Laat leeg om overal toe te staan.
            </p>
            {/* REQ-2 (Finding B-2): allow_any_origin toggle - bypasses the origin gate entirely.
                @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2 */}
            <div className="flex items-start gap-3 rounded-md border border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] p-3">
              <Checkbox
                id="widget-allow-any-origin"
                checked={form.allow_any_origin}
                onChange={(e) =>
                  setForm((p) => ({ ...p, allow_any_origin: e.target.checked }))
                }
              />
              <div className="space-y-1">
                <label
                  htmlFor="widget-allow-any-origin"
                  className="block cursor-pointer text-sm font-medium text-[var(--color-rl-dark)]"
                >
                  {m.admin_widgets_allow_any_origin_label()}
                </label>
                {form.allow_any_origin && (
                  <div className="flex items-start gap-1.5 text-xs text-[var(--color-warning)]">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px" />
                    {m.admin_widgets_allow_any_origin_warning()}
                  </div>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="widget-origins">
                {m.admin_widgets_widget_origins_label()}
                <span className="ml-2 text-xs font-normal text-gray-400">
                  (optioneel)
                </span>
              </Label>
              <Textarea
                id="widget-origins"
                value={form.allowed_origins_raw}
                onChange={(e) =>
                  setForm((p) => ({
                    ...p,
                    allowed_origins_raw: e.target.value,
                  }))
                }
                rows={4}
                placeholder={m.admin_widgets_widget_origins_placeholder()}
                className="font-mono"
              />
            </div>
            <p className="text-xs text-gray-400 pt-2">
              Na aanmaken vind je de share-link, embed-code en testknop op
              de Insluiten-tab van je widget - daar kun je deze lijst ook
              later nog aanpassen.
            </p>
          </section>
        )}

        {currentStepError && form.name.length > 0 && (
          <p className="text-sm text-[var(--color-destructive)]">
            {currentStepError}
          </p>
        )}

        {createMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {createMutation.error instanceof Error
              ? createMutation.error.message
              : m.admin_shared_error_generic()}
          </p>
        )}

        <div className="flex items-center justify-between pt-2 border-t border-gray-200 pt-4">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handlePrevious}
            disabled={currentIndex === 0}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.admin_shared_wizard_previous()}
          </Button>
          {isLastStep ? (
            <Button
              type="submit"
              disabled={createMutation.isPending || !!currentStepError}
            >
              {createMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {m.admin_shared_wizard_create()}
            </Button>
          ) : (
            <Button
              type="button"
              onClick={handleNext}
              disabled={!!currentStepError}
            >
              {m.admin_shared_wizard_next()}
              <ArrowRight className="h-4 w-4 ml-2" />
            </Button>
          )}
        </div>
      </form>
    </div>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400 mb-3">
      {children}
    </h3>
  )
}
