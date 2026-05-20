import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { useCreateWidget } from './-hooks'
import type { WidgetConfig } from './-types'
import { KbAccessEditor } from './_components/KbAccessEditor'

// TWD-style minimal create flow: just collect the bare minimum
// (name + description + KB access) needed to provision a row,
// then drop the admin on the detail page where the 5-tab editor
// (Algemeen / Kennisbanken / Vormgeving / Insluiten / Gevarenzone)
// handles every other field with live save and proper visual
// design. Removes the old 4-step wizard whose Appearance step
// missed brand color / theme / starters / position / disclaimer
// and whose Embed step missed the share-link + test button.

export const Route = createFileRoute('/admin/widgets/new')({
  component: NewWidgetPage,
})

interface FormState {
  name: string
  description: string
  kb_ids: number[]
}

const INITIAL_FORM: FormState = {
  name: '',
  description: '',
  kb_ids: [],
}

function NewWidgetPage() {
  const navigate = useNavigate()
  const createMutation = useCreateWidget()
  const [form, setForm] = useState<FormState>(INITIAL_FORM)

  function validate(): string | null {
    if (form.name.trim().length < 3) {
      return m.admin_shared_wizard_error_name_too_short()
    }
    if (form.kb_ids.length === 0) {
      return m.admin_shared_wizard_error_no_kb_selected()
    }
    return null
  }

  const validationError = validate()

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (validationError) return

    // Sensible defaults — the user refines everything on the detail
    // page tabs (brand color, starters, theme, position, etc.)
    const widgetConfig: WidgetConfig = {
      allowed_origins: [],
      title: form.name.trim(),
      welcome_message: '',
      system_prompt: '',
      css_variables: {},
      conversation_starters: [],
      hide_disclaimer: false,
      template_slug: null,
      primary_color: '#fcaa2d',
      theme: 'light',
      show_sources: true,
      show_meta: false,
      collect_user_info: false,
      widget_position: 'right',
    }

    createMutation.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim() || null,
        kb_ids: form.kb_ids,
        rate_limit_rpm: 60,
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

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_widgets_create()}
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Geef je widget een naam en kies de kennisbanken. Vormgeving,
            starters en deelinstellingen regel je op de volgende pagina.
          </p>
        </div>
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

      <form onSubmit={handleSubmit} className="space-y-6">
        <section className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="widget-name">{m.admin_shared_field_name()}</Label>
            <Input
              id="widget-name"
              value={form.name}
              onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
              placeholder="bv. Klantenservice bot"
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
              onChange={(e) =>
                setForm((p) => ({ ...p, description: e.target.value }))
              }
              rows={3}
              placeholder="Korte omschrijving — alleen zichtbaar in admin."
              className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)]"
            />
          </div>
        </section>

        <section className="space-y-2 pt-4 border-t border-gray-200">
          <Label>{m.admin_shared_wizard_step_kb_access()}</Label>
          <p className="text-xs text-gray-400">
            {m.admin_widgets_wizard_kb_access_intro_widget()}
          </p>
          <KbAccessEditor
            value={form.kb_ids}
            onChange={(kb_ids) => setForm((p) => ({ ...p, kb_ids }))}
          />
        </section>

        {validationError && form.name.length > 0 && (
          <p className="text-sm text-[var(--color-destructive)]">
            {validationError}
          </p>
        )}

        {createMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {createMutation.error instanceof Error
              ? createMutation.error.message
              : m.admin_shared_error_generic()}
          </p>
        )}

        <div className="flex items-center gap-3 pt-2">
          <Button
            type="submit"
            disabled={createMutation.isPending || !!validationError}
          >
            {createMutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            {m.admin_shared_wizard_create()}
          </Button>
          <button
            type="button"
            onClick={() => navigate({ to: '/admin/widgets' })}
            className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
          >
            {m.admin_shared_wizard_cancel()}
          </button>
        </div>
      </form>
    </div>
  )
}
