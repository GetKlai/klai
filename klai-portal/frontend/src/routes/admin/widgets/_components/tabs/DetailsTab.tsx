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

interface Template { slug: string; name: string; prompt_text: string }

interface Props {
  widget: WidgetDetailResponse
}

// TWD-parity Algemeen tab — two sections (Basis informatie + AI
// Configuratie). Appearance fields moved to AppearanceTab.
export function DetailsTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config
  const [name, setName] = useState(widget.name)
  const [description, setDescription] = useState(widget.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState(config.system_prompt)
  const [templateSlug, setTemplateSlug] = useState<string>(config.template_slug ?? '')

  const templatesQuery = useQuery<Template[]>({
    queryKey: ['app-templates'],
    queryFn: () => apiFetch<Template[]>('/api/app/templates'),
  })

  useEffect(() => {
    setName(widget.name)
    setDescription(widget.description ?? '')
    setSystemPrompt(config.system_prompt)
    setTemplateSlug(config.template_slug ?? '')
  }, [widget.name, widget.description, config.system_prompt, config.template_slug])

  const isDirty =
    name.trim() !== widget.name ||
    (description.trim() || null) !== widget.description ||
    systemPrompt.trim() !== config.system_prompt ||
    (templateSlug || null) !== (config.template_slug ?? null)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const next: WidgetConfig = {
      ...config,
      system_prompt: systemPrompt.trim(),
      template_slug: templateSlug || null,
    }
    updateMutation.mutate(
      {
        name: name.trim(),
        description: description.trim() || null,
        widget_config: next,
      },
      { onSuccess: () => toast.success(m.admin_shared_success_updated()) },
    )
  }

  function fillFromTemplate(slug: string) {
    setTemplateSlug(slug)
    const t = templatesQuery.data?.find((x) => x.slug === slug)
    if (t && !systemPrompt.trim()) setSystemPrompt(t.prompt_text)
  }

  const SectionHeading = ({ children }: { children: React.ReactNode }) => (
    <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400 mb-3">{children}</h3>
  )

  return (
    <form onSubmit={handleSubmit} className="space-y-8">
      {/* Basis Informatie */}
      <section>
        <SectionHeading>{m.admin_widgets_details_section_basics()}</SectionHeading>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="widget-name">{m.admin_shared_field_name()}</Label>
            <Input id="widget-name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="widget-description">{m.admin_widgets_details_role_scope_label()}</Label>
            <p className="text-xs text-gray-400">{m.admin_widgets_details_role_scope_help()}</p>
            <textarea
              id="widget-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)]"
            />
          </div>
        </div>
      </section>

      {/* AI Configuratie */}
      <section className="border-t border-gray-200 pt-6">
        <SectionHeading>{m.admin_widgets_details_section_ai()}</SectionHeading>
        <div className="space-y-4">
          {templatesQuery.data && templatesQuery.data.length > 0 && (
            <div className="space-y-1.5">
              <Label htmlFor="widget-template">{m.admin_widgets_widget_template_label()}</Label>
              <p className="text-xs text-gray-400">{m.admin_widgets_widget_template_help()}</p>
              <Select
                id="widget-template"
                value={templateSlug}
                onChange={(e) => fillFromTemplate(e.target.value)}
                className="max-w-md"
              >
                <option value="">{m.admin_widgets_widget_template_none()}</option>
                {templatesQuery.data.map((t) => (
                  <option key={t.slug} value={t.slug}>{t.name}</option>
                ))}
              </Select>
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="widget-system-prompt">{m.admin_widgets_widget_system_prompt_label()}</Label>
            <p className="text-xs text-gray-400">{m.admin_widgets_widget_system_prompt_help()}</p>
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
        </div>
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error ? updateMutation.error.message : m.admin_shared_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button type="submit" disabled={updateMutation.isPending || name.trim().length < 3 || !isDirty}>
          {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}
