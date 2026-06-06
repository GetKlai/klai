import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
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
  const [autoAcceptSameDomain, setAutoAcceptSameDomain] = useState(false)
  const [savedAutoAccept, setSavedAutoAccept] = useState(false)
  const mfaMutation = useMfaPolicyMutation(() => {
    setSavedMfa(true)
    setTimeout(() => setSavedMfa(false), 2500)
  })
  const autoAcceptMutation = useAutoAcceptSameDomainMutation(() => {
    setSavedAutoAccept(true)
    setTimeout(() => setSavedAutoAccept(false), 2500)
  })

  useEffect(() => {
    if (settings) {
      setSelectedMfa(settings.mfa_policy ?? 'optional')
      setAutoAcceptSameDomain(settings.auto_accept_same_domain)
    }
  }, [settings])

  const autoAcceptDirty =
    settings != null && autoAcceptSameDomain !== settings.auto_accept_same_domain

  return (
    <section className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-base font-display-bold text-gray-900">
          {m.admin_settings_security_title()}
        </h2>
        <p className="text-sm text-gray-400">
          {m.admin_settings_security_description()}
        </p>
      </div>
      <div className="space-y-6">
        {isLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : error ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : (
          <>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1 space-y-1.5">
                <Label htmlFor="settings-mfa">
                  {m.admin_settings_mfa_label()}
                </Label>
                <Select
                  id="settings-mfa"
                  value={selectedMfa}
                  onChange={(e) => setSelectedMfa(e.target.value as OrgSettings['mfa_policy'])}
                  containerClassName="max-w-xs"
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
              <Button
                className="w-fit"
                onClick={() => mfaMutation.mutate(selectedMfa)}
                disabled={
                  mfaMutation.isPending ||
                  savedMfa ||
                  selectedMfa === settings?.mfa_policy
                }
              >
                {savedMfa
                  ? m.admin_settings_saved()
                  : mfaMutation.isPending
                    ? m.admin_settings_saving()
                    : m.admin_settings_save()}
              </Button>
            </div>
            {mfaMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}

            {settings?.primary_domain && (
              <div className="border-t border-gray-200 pt-5 space-y-3">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <Label htmlFor="settings-auto-accept" className="cursor-pointer">
                      {m.admin_settings_auto_accept_label({ domain: settings.primary_domain })}
                    </Label>
                    <p className="text-xs text-gray-400">
                      {autoAcceptSameDomain
                        ? m.admin_settings_auto_accept_hint_on()
                        : m.admin_settings_auto_accept_hint_off()}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Switch
                      id="settings-auto-accept"
                      checked={autoAcceptSameDomain}
                      disabled={autoAcceptMutation.isPending}
                      onCheckedChange={setAutoAcceptSameDomain}
                    />
                    <Button
                      className="w-fit"
                      onClick={() => autoAcceptMutation.mutate(autoAcceptSameDomain)}
                      disabled={autoAcceptMutation.isPending || savedAutoAccept || !autoAcceptDirty}
                    >
                      {savedAutoAccept
                        ? m.admin_settings_saved()
                        : autoAcceptMutation.isPending
                          ? m.admin_settings_saving()
                          : m.admin_settings_save()}
                    </Button>
                  </div>
                </div>
                {autoAcceptMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
}
