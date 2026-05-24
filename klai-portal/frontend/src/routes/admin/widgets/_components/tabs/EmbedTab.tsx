import { useState, useEffect } from 'react'
import { Code2, Eye, Info, Link2, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { isValidOrigin, parseOrigins } from '@/features/widgets/config/origins'
import { buildWidgetEmbedSnippet } from '@/features/widgets/embed/snippet'
import * as m from '@/paraglide/messages'
import type { WidgetConfig, WidgetDetailResponse } from '../../-types'
import { useUpdateWidget } from '../../-hooks'

// Use the user's current portal origin (could be my.getklai.com OR a
// tenant subdomain like nerds-37376105.getklai.com). The widget's
// allowed_origins gate is exact-match, so adding a different host
// would not unblock the test page on the host the user is actually on.

interface Props {
  widget: WidgetDetailResponse
}

export function EmbedTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config

  const [originsRaw, setOriginsRaw] = useState(
    config.allowed_origins.join('\n'),
  )
  const [publicShareEnabled, setPublicShareEnabled] = useState(
    widget.public_share_enabled ?? false,
  )

  useEffect(() => {
    setOriginsRaw(config.allowed_origins.join('\n'))
    setPublicShareEnabled(widget.public_share_enabled ?? false)
  }, [config.allowed_origins, widget.public_share_enabled])

  const origins = parseOrigins(originsRaw)
  const invalidOrigins = origins.filter((o) => !isValidOrigin(o))

  const shareUrl = `${window.location.origin}/bot/${widget.widget_id}`
  const snippet = buildWidgetEmbedSnippet(
    widget.widget_id,
    config.title || undefined,
    config.welcome_message || undefined,
  )

  function copyShareLink() {
    if (!publicShareEnabled) return
    void navigator.clipboard.writeText(shareUrl)
    toast.success(m.admin_widgets_share_link_copied())
  }

  function copyEmbedCode() {
    void navigator.clipboard.writeText(snippet)
    toast.success(m.admin_widgets_embed_code_copied())
  }

  function openTest() {
    if (!publicShareEnabled) return
    // Embedded widget test (floating bubble preview) — TWD's
    // /widget-test?bot=...&w=... equivalent. Use the id query
    // param so the widget-test page loads the public-bot-config
    // and injects /widget/klai-chat.js with this widget_id.
    window.open(
      `/widget-test?id=${encodeURIComponent(widget.widget_id)}`,
      '_blank',
      'noopener,noreferrer',
    )
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const next: WidgetConfig = {
      ...config,
      allowed_origins: origins,
    }
    updateMutation.mutate(
      { widget_config: next, public_share_enabled: publicShareEnabled },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="rounded-xl border border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] p-4">
        <div className="flex items-center gap-2 mb-3">
          <Link2 className="h-4 w-4 text-[var(--color-rl-accent-dark)]" />
          <span className="text-sm font-medium text-[var(--color-rl-dark)]">
            {m.admin_widgets_share_link_title()}
          </span>
        </div>
        <div className="mb-3 flex items-start gap-3 rounded-md border border-[var(--color-rl-border)] bg-white p-3">
          <Checkbox
            id="widget-public-share"
            checked={publicShareEnabled}
            onChange={(e) => setPublicShareEnabled(e.target.checked)}
          />
          <div>
            <label htmlFor="widget-public-share" className="block cursor-pointer text-sm font-medium text-[var(--color-rl-dark)]">
              {m.admin_widgets_share_link_publish()}
            </label>
            <p className="text-xs text-gray-500">
              {m.admin_widgets_share_link_help()}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Input
            type="text"
            readOnly
            value={shareUrl}
            disabled={!publicShareEnabled}
            onFocus={(e) => e.currentTarget.select()}
            className="flex-1 border-[var(--color-rl-border)] bg-white font-mono text-xs text-[var(--color-rl-dark)] focus:ring-[var(--color-rl-accent)]/40"
          />
          <Button
            type="button"
            onClick={copyShareLink}
            disabled={!publicShareEnabled}
            size="sm"
          >
            {m.admin_widgets_share_link_copy()}
          </Button>
        </div>
      </section>

      <section className="rounded-xl border border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] p-4">
        <div className="flex items-center gap-2 mb-3">
          <Code2 className="h-4 w-4 text-[var(--color-rl-accent-dark)]" />
          <span className="text-sm font-medium text-[var(--color-rl-dark)]">
            {m.admin_widgets_embed_code_title()}
          </span>
        </div>
        <pre className="rounded-md border border-[var(--color-rl-border)] bg-white px-4 py-3 text-xs font-mono text-[var(--color-rl-dark)] overflow-x-auto whitespace-pre">
          {snippet}
        </pre>
        <div className="mt-3 flex items-stretch gap-2">
          <Button
            type="button"
            onClick={copyEmbedCode}
            variant="secondary"
            className="flex-1 border-[var(--color-rl-border)] text-[var(--color-rl-dark)] hover:bg-[var(--color-rl-bg)]"
          >
            {m.admin_widgets_embed_code_copy()}
          </Button>
          <Button
            type="button"
            onClick={openTest}
            disabled={!publicShareEnabled}
            className="bg-[var(--color-rl-accent)] text-[var(--color-rl-dark)] hover:bg-[var(--color-rl-accent-hover)]"
          >
            <Eye className="h-4 w-4" />
            {m.admin_widgets_test()}
          </Button>
        </div>
      </section>

      <section className="space-y-4 pt-4 border-t border-gray-200">
        <div className="space-y-1.5">
          <Label htmlFor="widget-origins">
            {m.admin_widgets_widget_origins_label()}
          </Label>
          <p className="text-xs text-gray-400">
            {m.admin_widgets_widget_origins_help()}
          </p>
          <Textarea
            id="widget-origins"
            value={originsRaw}
            onChange={(e) => setOriginsRaw(e.target.value)}
            rows={4}
            placeholder={m.admin_widgets_widget_origins_placeholder()}
            className="font-mono"
          />
          {invalidOrigins.length > 0 && (
            <p className="text-xs text-[var(--color-destructive)]">
              {m.admin_widgets_widget_invalid_origins({
                origins: invalidOrigins.join(', '),
              })}
            </p>
          )}
          {origins.length === 0 && (
            <div className="flex items-start gap-1.5 text-xs text-gray-400">
              <Info className="h-3.5 w-3.5 shrink-0 mt-px text-gray-400" />
              {m.admin_widgets_widget_origins_empty_warning()}
            </div>
          )}
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
        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}
