import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse, WidgetConfig } from '../../-types'
import { useUpdateWidget } from '../../-hooks'

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

function cssVarsFromRecord(record: Record<string, string>): CssVarRow[] {
  const rows: CssVarRow[] = Object.entries(record).map(([key, value]) => ({
    key: key as CssVarKey,
    value,
  }))
  if (rows.length === 0) rows.push({ key: '', value: '' })
  return rows
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

interface Props {
  widget: WidgetDetailResponse
}

export function AppearanceTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config

  const [title, setTitle] = useState(config.title)
  const [welcome, setWelcome] = useState(config.welcome_message)
  const [cssVarRows, setCssVarRows] = useState<CssVarRow[]>(cssVarsFromRecord(config.css_variables))

  useEffect(() => {
    setTitle(config.title)
    setWelcome(config.welcome_message)
    setCssVarRows(cssVarsFromRecord(config.css_variables))
  }, [config.title, config.welcome_message, config.css_variables])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const next: WidgetConfig = {
      ...config,
      title: title.trim(),
      welcome_message: welcome.trim(),
      css_variables: cssVarsToRecord(cssVarRows),
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
          <Label htmlFor="widget-title">{m.admin_widgets_widget_title_label()}</Label>
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.admin_widgets_widget_title_help()}
          </p>
          <Input
            id="widget-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
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
            value={welcome}
            onChange={(e) => setWelcome(e.target.value)}
            placeholder={m.admin_widgets_widget_welcome_placeholder()}
          />
        </div>
        <div className="space-y-2">
          <Label>{m.admin_widgets_widget_css_vars_label()}</Label>
          <div className="space-y-2">
            {cssVarRows.map((row, index) => (
              <div key={index} className="flex items-center gap-2">
                <select
                  value={row.key}
                  onChange={(e) =>
                    setCssVarRows((prev) =>
                      prev.map((r, i) =>
                        i === index ? { ...r, key: e.target.value as CssVarKey | '' } : r,
                      ),
                    )
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
                    setCssVarRows((prev) =>
                      prev.map((r, i) => (i === index ? { ...r, value: e.target.value } : r)),
                    )
                  }
                  placeholder="#000000"
                  className="flex-1 text-xs font-mono"
                />
                <button
                  type="button"
                  onClick={() => setCssVarRows((prev) => prev.filter((_, i) => i !== index))}
                  className="text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
                  aria-label={m.admin_widgets_widget_css_var_remove()}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          {cssVarRows.length < CSS_VAR_KEYS.length && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setCssVarRows((prev) => [...prev, { key: '', value: '' }])}
              className="text-xs"
            >
              {m.admin_widgets_widget_css_var_add()}
            </Button>
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
