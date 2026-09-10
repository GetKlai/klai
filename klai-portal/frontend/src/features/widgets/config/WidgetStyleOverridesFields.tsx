import * as m from '@/paraglide/messages'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'

type Props = {
  value: Record<string, string>
  onChange: (next: Record<string, string>) => void
  defaults?: Record<string, string>
  includeBaseColors?: boolean
}

type Field = {
  key: string
  label: () => string
  type: 'text' | 'number'
  unit?: 'px'
  min?: number
  max?: number
  step?: number
  tenantOnly?: boolean
}

const FONT_FAMILY_KEY = '--klai-font-family'
const FONT_BUNDLED = '"Klai Widget Geist", system-ui, sans-serif'
const FONT_SYSTEM = 'system-ui, sans-serif'

const fields: Field[] = [
  { key: '--klai-primary-color', label: m.admin_widgets_brand_color_label, type: 'text', tenantOnly: true },
  { key: '--klai-background-color', label: m.admin_widgets_background_color_label, type: 'text', tenantOnly: true },
  { key: '--klai-header-background', label: m.widget_style_header_background, type: 'text' },
  { key: '--klai-header-text-color', label: m.widget_style_header_text, type: 'text' },
  { key: '--klai-header-control-background', label: m.widget_style_header_control_background, type: 'text' },
  { key: '--klai-message-font-size', label: m.widget_style_message_font_size, type: 'number', unit: 'px', min: 12, max: 24 },
  { key: '--klai-input-font-size', label: m.widget_style_input_font_size, type: 'number', unit: 'px', min: 12, max: 24 },
  { key: '--klai-starter-font-size', label: m.widget_style_starter_font_size, type: 'number', unit: 'px', min: 12, max: 24 },
  { key: '--klai-message-gap', label: m.widget_style_message_gap, type: 'number', unit: 'px', min: 0, max: 40 },
  { key: '--klai-content-padding', label: m.widget_style_content_padding, type: 'number', unit: 'px', min: 0, max: 40 },
  { key: '--klai-border-radius', label: m.widget_style_border_radius, type: 'number', unit: 'px', min: 0, max: 32 },
  { key: '--klai-window-width', label: m.widget_style_window_width, type: 'number', unit: 'px', min: 300, max: 640 },
  { key: '--klai-window-height', label: m.widget_style_window_height, type: 'number', unit: 'px', min: 320, max: 900 },
  { key: '--klai-message-line-height', label: m.widget_style_line_height, type: 'number', min: 1.2, max: 2.4, step: 0.01 },
]

function withoutUnit(value: string | undefined, unit?: string) {
  return unit && value?.endsWith(unit) ? value.slice(0, -unit.length) : value ?? ''
}

export function WidgetStyleOverridesFields({ value, onChange, defaults = {}, includeBaseColors = false }: Props) {
  const update = (field: Field, raw: string) => {
    const next = { ...value }
    if (!raw.trim()) delete next[field.key]
    else next[field.key] = field.unit ? `${raw}${field.unit}` : raw
    onChange(next)
  }

  return (
    <details className="rounded-lg border border-gray-200">
      <summary className="klai-hover cursor-pointer rounded-lg px-4 py-3 text-sm font-medium text-gray-900">
        {m.widget_style_advanced_title()}
      </summary>
      <div className="space-y-1.5 border-t border-gray-200 p-4">
        <Label htmlFor="widget-style-font-family">{m.widget_style_font_family()}</Label>
        <Select
          id="widget-style-font-family"
          value={value[FONT_FAMILY_KEY] ?? ''}
          onChange={(event) => {
            const next = { ...value }
            if (!event.currentTarget.value) delete next[FONT_FAMILY_KEY]
            else next[FONT_FAMILY_KEY] = event.currentTarget.value
            onChange(next)
          }}
        >
          <option value="">{m.widget_style_font_inherit()}</option>
          <option value={FONT_BUNDLED}>{m.widget_style_font_bundled()}</option>
          <option value={FONT_SYSTEM}>{m.widget_style_font_system()}</option>
        </Select>
        <p className="text-xs text-gray-600">{m.widget_style_font_help()}</p>
      </div>
      <div className="grid gap-4 border-t border-gray-200 p-4 sm:grid-cols-2">
        {fields.filter((field) => includeBaseColors || !field.tenantOnly).map((field) => {
          const id = `widget-style-${field.key.slice(7)}`
          return (
            <div key={field.key} className="space-y-1.5">
              <Label htmlFor={id}>
                {field.label()}{field.unit ? <span className="ml-1 font-normal text-gray-600">({field.unit})</span> : null}
              </Label>
              <Input
                id={id}
                type={field.type}
                pattern={field.type === 'text' ? '#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})' : undefined}
                min={field.min}
                max={field.max}
                step={field.step ?? (field.unit === 'px' ? 0.1 : undefined)}
                value={withoutUnit(value[field.key], field.unit)}
                placeholder={withoutUnit(defaults[field.key], field.unit)}
                onChange={(event) => update(field, event.currentTarget.value)}
              />
            </div>
          )
        })}
      </div>
    </details>
  )
}
