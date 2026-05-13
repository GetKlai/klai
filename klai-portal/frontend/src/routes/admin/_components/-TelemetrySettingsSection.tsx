import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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

  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.admin_settings_telemetry_title()}</CardTitle>
        <CardDescription>
          {m.admin_settings_telemetry_description()}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : error ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : (
          <>
            <p className="text-sm text-gray-400">
              {m.admin_settings_telemetry_current({
                level:
                  settings?.telemetry_level === 'off'
                    ? m.admin_settings_telemetry_off_name()
                    : settings?.telemetry_level === 'full'
                      ? m.admin_settings_telemetry_full_name()
                      : m.admin_settings_telemetry_shadow_name(),
              })}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="settings-telemetry-level">
                {m.admin_settings_telemetry_label()}
              </Label>
              <Select
                id="settings-telemetry-level"
                value={selectedTelemetry}
                onChange={(e) => setSelectedTelemetry(e.target.value as TelemetryLevel)}
                className="max-w-xs"
              >
                <option value="off">{m.admin_settings_telemetry_off_name()}</option>
                <option value="shadow">{m.admin_settings_telemetry_shadow_name()}</option>
                <option value="full">{m.admin_settings_telemetry_full_name()}</option>
              </Select>
              <p className="text-xs text-gray-400">
                {selectedTelemetry === 'off' && m.admin_settings_telemetry_off_hint()}
                {selectedTelemetry === 'shadow' && m.admin_settings_telemetry_shadow_hint()}
                {selectedTelemetry === 'full' && m.admin_settings_telemetry_full_hint()}
              </p>
            </div>
            {telemetryMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}
            <Button
              onClick={() => telemetryMutation.mutate(selectedTelemetry)}
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
            <p className="text-xs text-gray-400">
              <a href="/privacy" className="underline">
                {m.admin_settings_telemetry_privacy_link()}
              </a>
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
