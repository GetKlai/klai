import { useState, useEffect } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { WidgetConfig, WidgetDetailResponse } from '../../-types'
import { useUpdateWidget } from '../../-hooks'
import { EmbedSnippet } from '../EmbedSnippet'

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
        onSuccess: () => toast.success(m.admin_integrations_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="widget-origins">
            {m.admin_integrations_widget_origins_label()}
          </Label>
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.admin_integrations_widget_origins_help()}
          </p>
          <textarea
            id="widget-origins"
            value={originsRaw}
            onChange={(e) => setOriginsRaw(e.target.value)}
            rows={4}
            placeholder={m.admin_integrations_widget_origins_placeholder()}
            className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm font-mono text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)]"
          />
          {invalidOrigins.length > 0 && (
            <p className="text-xs text-[var(--color-destructive)]">
              {m.admin_integrations_widget_invalid_origins({ origins: invalidOrigins.join(', ') })}
            </p>
          )}
          {origins.length === 0 && (
            <div className="flex items-start gap-1.5 text-xs text-[var(--color-muted-foreground)]">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px text-[var(--color-destructive)]" />
              {m.admin_integrations_widget_origins_empty_warning()}
            </div>
          )}
        </div>
      </section>

      <section className="space-y-4 pt-4 border-t border-[var(--color-border)]">
        <EmbedSnippet
          widgetId={widget.widget_id}
          title={config.title || undefined}
          welcomeMessage={config.welcome_message || undefined}
        />
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_integrations_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_integrations_save()}
        </Button>
      </div>
    </form>
  )
}
