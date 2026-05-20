import { useState, useEffect } from 'react'
import { AlertTriangle, Code2, Eye, Link2, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { WidgetConfig, WidgetDetailResponse } from '../../-types'
import { useUpdateWidget } from '../../-hooks'

// Use the user's current portal origin (could be my.getklai.com OR a
// tenant subdomain like nerds-37376105.getklai.com). The widget's
// allowed_origins gate is exact-match, so adding a different host
// would not unblock the test page on the host the user is actually on.

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

function buildSnippet(
  widgetId: string,
  title?: string,
  welcomeMessage?: string,
): string {
  const attrs: string[] = [`  src="https://my.getklai.com/widget/klai-chat.js"`]
  attrs.push(`  data-widget-id="${widgetId}"`)
  if (title) attrs.push(`  data-title="${title}"`)
  if (welcomeMessage) attrs.push(`  data-welcome="${welcomeMessage}"`)
  return `<script\n${attrs.join('\n')}\n></script>`
}

interface Props {
  widget: WidgetDetailResponse
}

export function EmbedTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config

  const [originsRaw, setOriginsRaw] = useState(
    config.allowed_origins.join('\n'),
  )

  useEffect(() => {
    setOriginsRaw(config.allowed_origins.join('\n'))
  }, [config.allowed_origins])

  const origins = parseOrigins(originsRaw)
  const invalidOrigins = origins.filter((o) => !isValidOrigin(o))

  const shareUrl = `${window.location.origin}/bot/${widget.widget_id}`
  const snippet = buildSnippet(
    widget.widget_id,
    config.title || undefined,
    config.welcome_message || undefined,
  )

  function copyShareLink() {
    void navigator.clipboard.writeText(shareUrl)
    toast.success('Link gekopieerd')
  }

  function copyEmbedCode() {
    void navigator.clipboard.writeText(snippet)
    toast.success('Embed code gekopieerd')
  }

  function openTest() {
    window.open(`/bot/${widget.widget_id}`, '_blank', 'noopener,noreferrer')
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const next: WidgetConfig = {
      ...config,
      allowed_origins: origins,
    }
    updateMutation.mutate(
      { widget_config: next },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Deelbare link — TWD-style green card. Public bot URL works for
          anyone with the link, no auth required, no origin gymnastics. */}
      <section className="rounded-xl border border-[var(--color-success)]/30 bg-[var(--color-success-bg)]/40 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Link2 className="h-4 w-4 text-[var(--color-success)]" />
          <span className="text-sm font-medium text-gray-900">
            Deelbare link
          </span>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            readOnly
            value={shareUrl}
            onFocus={(e) => e.currentTarget.select()}
            className="flex-1 rounded-md border border-gray-200 bg-white px-3 py-2 font-mono text-xs text-gray-700 outline-none focus:ring-2 focus:ring-[var(--color-success)]/30"
          />
          <button
            type="button"
            onClick={copyShareLink}
            className="inline-flex items-center justify-center rounded-full bg-[var(--color-success)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
          >
            Kopieer
          </button>
        </div>
      </section>

      {/* Embed code (widget script) — TWD-style cream card. */}
      <section className="rounded-xl border border-gray-200 bg-[var(--color-rl-cream)] p-4">
        <div className="flex items-center gap-2 mb-3">
          <Code2 className="h-4 w-4 text-gray-500" />
          <span className="text-sm font-medium text-gray-900">
            Embed code (widget script)
          </span>
        </div>
        <pre className="rounded-md border border-gray-200 bg-white px-4 py-3 text-xs font-mono text-gray-900 overflow-x-auto whitespace-pre">
          {snippet}
        </pre>
        <div className="mt-3 flex items-stretch gap-2">
          <button
            type="button"
            onClick={copyEmbedCode}
            className="flex-1 inline-flex items-center justify-center rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-900 transition-colors hover:bg-gray-50"
          >
            Kopieer embed code
          </button>
          <button
            type="button"
            onClick={openTest}
            className="inline-flex items-center gap-1.5 rounded-full bg-[var(--color-success)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
          >
            <Eye className="h-4 w-4" />
            Test
          </button>
        </div>
      </section>

      {/* Advanced — allowed origins. */}
      <section className="space-y-4 pt-4 border-t border-gray-200">
        <div className="space-y-1.5">
          <Label htmlFor="widget-origins">
            {m.admin_widgets_widget_origins_label()}
          </Label>
          <p className="text-xs text-gray-400">
            {m.admin_widgets_widget_origins_help()}
          </p>
          <textarea
            id="widget-origins"
            value={originsRaw}
            onChange={(e) => setOriginsRaw(e.target.value)}
            rows={4}
            placeholder={m.admin_widgets_widget_origins_placeholder()}
            className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm font-mono text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)]"
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
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px text-[var(--color-destructive)]" />
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
