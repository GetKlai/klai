import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import {
  useTelemetryLevelMutation,
  type OrgSettings,
  type TelemetryLevel,
} from '../-settings-hooks'

interface TelemetrySettingsSectionProps {
  settings: OrgSettings | undefined
  isLoading: boolean
  error: unknown
}

export function TelemetrySettingsSection({
  settings,
  isLoading,
  error,
}: TelemetrySettingsSectionProps) {
  const [selectedTelemetry, setSelectedTelemetry] = useState<TelemetryLevel>('shadow')
  const [savedTelemetry, setSavedTelemetry] = useState(false)
  const telemetryMutation = useTelemetryLevelMutation(() => {
    setSavedTelemetry(true)
    setTimeout(() => setSavedTelemetry(false), 2500)
  })

  useEffect(() => {
    if (settings?.telemetry_level) {
      setSelectedTelemetry(settings.telemetry_level)
    }
  }, [settings?.telemetry_level])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    telemetryMutation.mutate(selectedTelemetry)
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-display-bold text-gray-900">
          {m.admin_settings_telemetry_title()}
        </h2>
      </div>
      <form onSubmit={handleSubmit} className="space-y-4">
        {isLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : error ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="settings-telemetry-level">
                {m.admin_settings_telemetry_label()}
              </Label>
              <Select
                id="settings-telemetry-level"
                value={selectedTelemetry}
                onChange={(e) => setSelectedTelemetry(e.target.value as TelemetryLevel)}
                containerClassName="max-w-xs"
              >
                <option value="off">{m.admin_settings_telemetry_off_name()}</option>
                <option value="shadow">{m.admin_settings_telemetry_shadow_name()}</option>
                <option value="full">{m.admin_settings_telemetry_full_name()}</option>
              </Select>
              <p className="text-xs text-gray-400">
                {m.admin_settings_telemetry_help()}{' '}
                <a href="/privacy" className="underline">
                  {m.admin_settings_telemetry_privacy_link()}
                </a>
              </p>
            </div>
            {telemetryMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}
            <div className="pt-2">
              <Button
                type="submit"
                disabled={
                  telemetryMutation.isPending ||
                  savedTelemetry ||
                  selectedTelemetry === settings?.telemetry_level
                }
              >
                {savedTelemetry
                  ? m.admin_settings_saved()
                  : telemetryMutation.isPending
                    ? m.admin_settings_saving()
                    : m.admin_settings_save()}
              </Button>
            </div>
          </>
        )}
      </form>
    </section>
  )
}
