import { useState, useEffect } from 'react'
import { AlertTriangle, ExternalLink, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { WidgetConfig, WidgetDetailResponse } from '../../-types'
import { useUpdateWidget } from '../../-hooks'
import { EmbedSnippet } from '../EmbedSnippet'

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

interface Props {
  widget: WidgetDetailResponse
}

export function EmbedTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config

  const [originsRaw, setOriginsRaw] = useState(config.allowed_origins.join('\n'))

  useEffect(() => {
    setOriginsRaw(config.allowed_origins.join('\n'))
  }, [config.allowed_origins])

  const origins = parseOrigins(originsRaw)
  const invalidOrigins = origins.filter((o) => !isValidOrigin(o))

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
      <section className="space-y-4">
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
              {m.admin_widgets_widget_invalid_origins({ origins: invalidOrigins.join(', ') })}
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

      <section className="space-y-4 pt-4 border-t border-gray-200">
        <EmbedSnippet
          widgetId={widget.widget_id}
          title={config.title || undefined}
          welcomeMessage={config.welcome_message || undefined}
        />
        <div>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              // Auto-add the current portal origin to allowed_origins
              // (idempotent) before opening the test tab. The widget's
              // origin check is exact-match — adding the wrong host
              // would not unblock the test page where the user actually
              // lands. Use window.location.origin so this works on
              // my.getklai.com AND tenant subdomains like
              // nerds-37376105.getklai.com.
              const portalOrigin = window.location.origin
              const hasPortal = origins.includes(portalOrigin)
              const openTest = () => {
                window.open(`/admin/widgets/${widget.id}/test`, '_blank', 'noopener,noreferrer')
              }
              if (hasPortal) {
                openTest()
                return
              }
              const next: WidgetConfig = {
                ...config,
                allowed_origins: [...origins, portalOrigin],
              }
              updateMutation.mutate(
                { widget_config: next },
                {
                  onSuccess: () => {
                    setOriginsRaw([...origins, portalOrigin].join('\n'))
                    openTest()
                  },
                  onError: () => toast.error(m.admin_shared_error_generic()),
                },
              )
            }}
            disabled={updateMutation.isPending || origins.length === 0}
          >
            {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {!updateMutation.isPending && <ExternalLink className="mr-2 h-4 w-4" />}
            {m.admin_widgets_test_button()}
          </Button>
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
