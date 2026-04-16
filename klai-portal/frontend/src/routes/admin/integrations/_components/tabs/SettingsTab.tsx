import { useState, useEffect } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { IntegrationDetailResponse, WidgetConfig } from '../../-types'
import { useUpdateIntegration } from '../../-hooks'

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

function cssVarsFromRecord(record: Record<string, string>): CssVarRow[] {
  const rows: CssVarRow[] = Object.entries(record).map(([key, value]) => ({
    key: key as CssVarKey,
    value,
  }))
  if (rows.length === 0) {
    rows.push({ key: '', value: '' })
  }
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

interface SettingsTabProps {
  integration: IntegrationDetailResponse
}

export function SettingsTab({ integration }: SettingsTabProps) {
  const updateMutation = useUpdateIntegration(String(integration.id))
  const isWidget = integration.integration_type === 'widget'
  const isDisabled = integration.active === false || updateMutation.isPending

  // API state
  const [rateLimit, setRateLimit] = useState(integration.rate_limit_rpm)

  // Widget state
  const widgetConfig = integration.widget_config ?? {
    allowed_origins: [],
    title: '',
    welcome_message: '',
    css_variables: {},
  }
  const [originsRaw, setOriginsRaw] = useState(
    widgetConfig.allowed_origins.join('\n'),
  )
  const [title, setTitle] = useState(widgetConfig.title)
  const [welcomeMessage, setWelcomeMessage] = useState(widgetConfig.welcome_message)
  const [cssVarRows, setCssVarRows] = useState<CssVarRow[]>(
    cssVarsFromRecord(widgetConfig.css_variables),
  )

  useEffect(() => {
    setRateLimit(integration.rate_limit_rpm)
    const cfg = integration.widget_config ?? {
      allowed_origins: [],
      title: '',
      welcome_message: '',
      css_variables: {},
    }
    setOriginsRaw(cfg.allowed_origins.join('\n'))
    setTitle(cfg.title)
    setWelcomeMessage(cfg.welcome_message)
    setCssVarRows(cssVarsFromRecord(cfg.css_variables))
  }, [integration])

  const origins = parseOrigins(originsRaw)
  const invalidOrigins = origins.filter((o) => !isValidOrigin(o))

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (isWidget) {
      const config: WidgetConfig = {
        allowed_origins: origins,
        title: title.trim(),
        welcome_message: welcomeMessage.trim(),
        css_variables: cssVarsToRecord(cssVarRows),
      }
      updateMutation.mutate(
        { widget_config: config },
        {
          onSuccess: () => {
            toast.success(m.admin_integrations_success_updated())
          },
        },
      )
    } else {
      updateMutation.mutate(
        { rate_limit_rpm: rateLimit },
        {
          onSuccess: () => {
            toast.success(m.admin_integrations_success_updated())
          },
        },
      )
    }
  }

  if (!isWidget) {
    // API: rate limit only
    return (
      <form onSubmit={handleSubmit} className="space-y-6">
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_rate_limit()}
          </h2>
          <div className="space-y-1.5">
            <Label htmlFor="rate-limit">
              {m.admin_integrations_field_rate_limit()}
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id="rate-limit"
                type="number"
                min={10}
                max={600}
                value={rateLimit}
                onChange={(e) => setRateLimit(Number(e.target.value))}
                disabled={isDisabled}
                className="max-w-[8rem]"
              />
              <span className="text-sm text-[var(--color-muted-foreground)]">
                {m.admin_integrations_rate_limit_unit()}
              </span>
            </div>
          </div>
        </section>

        {updateMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {updateMutation.error instanceof Error
              ? updateMutation.error.message
              : m.admin_integrations_error_generic()}
          </p>
        )}

        {integration.active && (
          <div className="pt-2">
            <Button type="submit" disabled={updateMutation.isPending}>
              {updateMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {m.admin_integrations_save()}
            </Button>
          </div>
        )}
      </form>
    )
  }

  // Widget: appearance
  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <h2 className="text-sm font-medium text-[var(--color-foreground)]">
          {m.admin_integrations_section_widget_appearance()}
        </h2>

        {/* Allowed origins */}
        <div className="space-y-1.5">
          <Label htmlFor="widget-origins">
            {m.admin_integrations_widget_origins_label()}
          </Label>
          <textarea
            id="widget-origins"
            value={originsRaw}
            onChange={(e) => setOriginsRaw(e.target.value)}
            rows={4}
            disabled={isDisabled}
            placeholder={m.admin_integrations_widget_origins_placeholder()}
            className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm font-mono text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
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

        {/* Title */}
        <div className="space-y-1.5">
          <Label htmlFor="widget-title">
            {m.admin_integrations_widget_title_label()}
          </Label>
          <Input
            id="widget-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={isDisabled}
          />
        </div>

        {/* Welcome message */}
        <div className="space-y-1.5">
          <Label htmlFor="widget-welcome">
            {m.admin_integrations_widget_welcome_label()}
          </Label>
          <Input
            id="widget-welcome"
            value={welcomeMessage}
            onChange={(e) => setWelcomeMessage(e.target.value)}
            disabled={isDisabled}
          />
        </div>

        {/* CSS variables */}
        <div className="space-y-2">
          <Label>{m.admin_integrations_widget_css_vars_label()}</Label>
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
                  disabled={isDisabled}
                  className="flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-xs font-mono text-[var(--color-foreground)] outline-none focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="">{m.admin_integrations_widget_css_var_placeholder()}</option>
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
                  disabled={isDisabled}
                  placeholder="#000000"
                  className="flex-1 text-xs font-mono"
                />
                <button
                  type="button"
                  onClick={() =>
                    setCssVarRows((prev) => prev.filter((_, i) => i !== index))
                  }
                  disabled={isDisabled}
                  className="text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors disabled:opacity-40"
                  aria-label={m.admin_integrations_widget_css_var_remove()}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          {!isDisabled && cssVarRows.length < CSS_VAR_KEYS.length && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setCssVarRows((prev) => [...prev, { key: '', value: '' }])}
              className="text-xs"
            >
              {m.admin_integrations_widget_css_var_add()}
            </Button>
          )}
        </div>
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_integrations_error_generic()}
        </p>
      )}

      {integration.active && (
        <div className="pt-2">
          <Button type="submit" disabled={updateMutation.isPending}>
            {updateMutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            {m.admin_integrations_save()}
          </Button>
        </div>
      )}
    </form>
  )
}
