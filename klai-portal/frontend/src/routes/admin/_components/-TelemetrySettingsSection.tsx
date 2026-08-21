import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import type { TelemetryLevel } from '../-settings-hooks'

interface TelemetrySettingsSectionProps {
  selectedTelemetry: TelemetryLevel
  onTelemetryChange: (level: TelemetryLevel) => void
  isLoading: boolean
  error: unknown
  disabled: boolean
}

// Presentational + controlled: staged value and mutation both live in the
// parent PrivacySettingsTab, which owns this tab's single save button.
export function TelemetrySettingsSection({
  selectedTelemetry,
  onTelemetryChange,
  isLoading,
  error,
  disabled,
}: TelemetrySettingsSectionProps) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-display-bold text-gray-900">
          {m.admin_settings_telemetry_title()}
        </h2>
      </div>
      {isLoading ? (
        <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
      ) : error ? (
        <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
      ) : (
        <div className="space-y-1.5">
          <Label htmlFor="settings-telemetry-level">
            {m.admin_settings_telemetry_label()}
          </Label>
          <Select
            id="settings-telemetry-level"
            value={selectedTelemetry}
            disabled={disabled}
            onChange={(e) => onTelemetryChange(e.target.value as TelemetryLevel)}
            containerClassName="max-w-xs"
          >
            <option value="off">{m.admin_settings_telemetry_off_name()}</option>
            <option value="shadow">{m.admin_settings_telemetry_shadow_name()}</option>
            <option value="full">{m.admin_settings_telemetry_full_name()}</option>
          </Select>
          <p className="text-xs text-gray-400">
            {m.admin_settings_telemetry_help()}{' '}
            <a href="/app/docs/klai-help/8b57605d-675c-48cd-b33b-3ee1705c33a6" className="underline">
              {m.admin_settings_telemetry_privacy_link()}
            </a>
          </p>
        </div>
      )}
    </section>
  )
}
