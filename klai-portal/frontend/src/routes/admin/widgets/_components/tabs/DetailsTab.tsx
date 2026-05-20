import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse, WidgetConfig } from '../../-types'
import { useUpdateWidget } from '../../-hooks'

interface Template {
  slug: string
  name: string
  prompt_text: string
}

const MAX_STARTERS = 6

interface Props {
  widget: WidgetDetailResponse
}

export function DetailsTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config
  const [name, setName] = useState(widget.name)
  const [description, setDescription] = useState(widget.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState(config.system_prompt)
  const [startersRaw, setStartersRaw] = useState((config.conversation_starters ?? []).join('\n'))
  const [hideDisclaimer, setHideDisclaimer] = useState(config.hide_disclaimer ?? false)
  const [templateSlug, setTemplateSlug] = useState<string>(config.template_slug ?? '')

  // Templates list — for the "Vul vanuit template" picker. Same API the
  // chatbar Templates dropdown uses.
  const templatesQuery = useQuery<Template[]>({
    queryKey: ['app-templates'],
    queryFn: () => apiFetch<Template[]>('/api/app/templates'),
  })

  useEffect(() => {
    setName(widget.name)
    setDescription(widget.description ?? '')
    setSystemPrompt(config.system_prompt)
    setStartersRaw((config.conversation_starters ?? []).join('\n'))
    setHideDisclaimer(config.hide_disclaimer ?? false)
    setTemplateSlug(config.template_slug ?? '')
  }, [widget.name, widget.description, config.system_prompt, config.conversation_starters, config.hide_disclaimer, config.template_slug])

  const starters = startersRaw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, MAX_STARTERS)

  const startersChanged =
    JSON.stringify(starters) !== JSON.stringify(config.conversation_starters ?? [])
  const templateChanged = (templateSlug || null) !== (config.template_slug ?? null)
  const hideDisclaimerChanged = hideDisclaimer !== (config.hide_disclaimer ?? false)

  const isDirty =
    name.trim() !== widget.name ||
    (description.trim() || null) !== widget.description ||
    systemPrompt.trim() !== config.system_prompt ||
    startersChanged ||
    templateChanged ||
    hideDisclaimerChanged

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const nextConfig: WidgetConfig = {
      ...config,
      system_prompt: systemPrompt.trim(),
      conversation_starters: starters,
      hide_disclaimer: hideDisclaimer,
      template_slug: templateSlug || null,
    }
    updateMutation.mutate(
      {
        name: name.trim(),
        description: description.trim() || null,
        widget_config: nextConfig,
      },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
      },
    )
  }

  function fillFromTemplate(slug: string) {
    setTemplateSlug(slug)
    const t = templatesQuery.data?.find((x) => x.slug === slug)
    if (t && !systemPrompt.trim()) {
      setSystemPrompt(t.prompt_text)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="widget-name">{m.admin_shared_field_name()}</Label>
          <Input
            id="widget-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="widget-description">{m.admin_shared_field_description()}</Label>
          <textarea
            id="widget-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)]"
          />
        </div>

        {/* Template picker — soft-link: picking a template fills the
            system_prompt textarea when empty. Stored on the widget so
            the chat backend can also append the template at runtime. */}
        {templatesQuery.data && templatesQuery.data.length > 0 && (
          <div className="space-y-1.5">
            <Label htmlFor="widget-template">{m.admin_widgets_widget_template_label()}</Label>
            <p className="text-xs text-gray-400">
              {m.admin_widgets_widget_template_help()}
            </p>
            <Select
              id="widget-template"
              value={templateSlug}
              onChange={(e) => fillFromTemplate(e.target.value)}
              className="max-w-md"
            >
              <option value="">{m.admin_widgets_widget_template_none()}</option>
              {templatesQuery.data.map((t) => (
                <option key={t.slug} value={t.slug}>
                  {t.name}
                </option>
              ))}
            </Select>
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="widget-system-prompt">{m.admin_widgets_widget_system_prompt_label()}</Label>
          <p className="text-xs text-gray-400">
            {m.admin_widgets_widget_system_prompt_help()}
          </p>
          <textarea
            id="widget-system-prompt"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={6}
            maxLength={4000}
            placeholder={m.admin_widgets_widget_system_prompt_placeholder()}
            className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)]"
          />
        </div>

        {/* Conversation starter chips — TWD-style suggestion pills on the
            empty state. Max 6, one per line. Empty lines ignored. */}
        <div className="space-y-1.5">
          <Label htmlFor="widget-starters">{m.admin_widgets_widget_starters_label()}</Label>
          <p className="text-xs text-gray-400">{m.admin_widgets_widget_starters_help()}</p>
          <textarea
            id="widget-starters"
            value={startersRaw}
            onChange={(e) => setStartersRaw(e.target.value)}
            rows={4}
            placeholder={m.admin_widgets_widget_starters_placeholder()}
            className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)]"
          />
          <p className="text-xs text-gray-400">
            {starters.length}/{MAX_STARTERS}
          </p>
        </div>

        <div className="flex items-start gap-3 pt-1">
          <input
            id="widget-hide-disclaimer"
            type="checkbox"
            checked={hideDisclaimer}
            onChange={(e) => setHideDisclaimer(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-[var(--color-rl-accent)]"
          />
          <div>
            <Label htmlFor="widget-hide-disclaimer" className="cursor-pointer">
              {m.admin_widgets_widget_hide_disclaimer_label()}
            </Label>
            <p className="text-xs text-gray-400">
              {m.admin_widgets_widget_hide_disclaimer_help()}
            </p>
          </div>
        </div>
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_shared_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button
          type="submit"
          disabled={updateMutation.isPending || name.trim().length < 3 || !isDirty}
        >
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}
