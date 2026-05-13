import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import {
  useAutoAcceptSameDomainMutation,
  useMfaPolicyMutation,
  type OrgSettings,
} from '../-settings-hooks'

interface SecuritySettingsSectionProps {
  settings: OrgSettings | undefined
  isLoading: boolean
  error: unknown
}

export function SecuritySettingsSection({
  settings,
  isLoading,
  error,
}: SecuritySettingsSectionProps) {
  const [selectedMfa, setSelectedMfa] = useState<OrgSettings['mfa_policy']>('optional')
  const [savedMfa, setSavedMfa] = useState(false)
  const mfaMutation = useMfaPolicyMutation(() => {
    setSavedMfa(true)
    setTimeout(() => setSavedMfa(false), 2500)
  })
  const autoAcceptMutation = useAutoAcceptSameDomainMutation()

  useEffect(() => {
    if (settings) {
      setSelectedMfa(settings.mfa_policy ?? 'optional')
    }
  }, [settings])

  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.admin_settings_security_title()}</CardTitle>
        <CardDescription>
          {m.admin_settings_security_description()}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {isLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : error ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="settings-mfa">
                {m.admin_settings_mfa_label()}
              </Label>
              <Select
                id="settings-mfa"
                value={selectedMfa}
                onChange={(e) => setSelectedMfa(e.target.value as OrgSettings['mfa_policy'])}
                className="max-w-xs"
              >
                <option value="optional">{m.admin_settings_mfa_optional()}</option>
                <option value="recommended">{m.admin_settings_mfa_recommended()}</option>
                <option value="required">{m.admin_settings_mfa_required()}</option>
              </Select>
              <p className="text-xs text-gray-400">
                {selectedMfa === 'optional' && m.admin_settings_mfa_optional_hint()}
                {selectedMfa === 'recommended' && m.admin_settings_mfa_recommended_hint()}
                {selectedMfa === 'required' && m.admin_settings_mfa_required_hint()}
              </p>
            </div>
            {mfaMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}
            <Button
              onClick={() => mfaMutation.mutate(selectedMfa)}
              disabled={mfaMutation.isPending || savedMfa}
            >
              {savedMfa
                ? m.admin_settings_saved()
                : mfaMutation.isPending
                  ? m.admin_settings_saving()
                  : m.admin_settings_save()}
            </Button>

            {settings?.primary_domain && (
              <div className="border-t pt-4 space-y-1.5">
                <div className="flex items-center justify-between gap-4">
                  <div className="space-y-0.5">
                    <Label htmlFor="settings-auto-accept" className="cursor-pointer">
                      {m.admin_settings_auto_accept_label({ domain: settings.primary_domain })}
                    </Label>
                    <p className="text-xs text-gray-400">
                      {settings.auto_accept_same_domain
                        ? m.admin_settings_auto_accept_hint_on()
                        : m.admin_settings_auto_accept_hint_off()}
                    </p>
                  </div>
                  <button
                    id="settings-auto-accept"
                    type="button"
                    role="switch"
                    aria-checked={settings.auto_accept_same_domain}
                    disabled={autoAcceptMutation.isPending}
                    onClick={() => autoAcceptMutation.mutate(!settings.auto_accept_same_domain)}
                    className={[
                      'relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full',
                      'border-2 border-transparent transition-colors focus-visible:outline-none',
                      'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                      'disabled:cursor-not-allowed disabled:opacity-50',
                      settings.auto_accept_same_domain
                        ? 'bg-primary'
                        : 'bg-input',
                    ].join(' ')}
                  >
                    <span
                      className={[
                        'pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0',
                        'transition-transform',
                        settings.auto_accept_same_domain ? 'translate-x-5' : 'translate-x-0',
                      ].join(' ')}
                    />
                  </button>
                </div>
                {autoAcceptMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
                )}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
