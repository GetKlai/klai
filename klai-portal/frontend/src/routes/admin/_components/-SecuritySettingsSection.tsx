import { useEffect, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import {
  useSecuritySettingsMutation,
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
  const [autoAcceptSameDomain, setAutoAcceptSameDomain] = useState(false)
  const [savedSecurity, setSavedSecurity] = useState(false)
  const securityMutation = useSecuritySettingsMutation(() => {
    setSavedSecurity(true)
    setTimeout(() => setSavedSecurity(false), 2500)
  })

  useEffect(() => {
    if (settings) {
      setSelectedMfa(settings.mfa_policy ?? 'optional')
      setAutoAcceptSameDomain(settings.auto_accept_same_domain)
    }
  }, [settings])

  const securityDirty =
    settings != null &&
    (selectedMfa !== settings.mfa_policy ||
      autoAcceptSameDomain !== settings.auto_accept_same_domain)

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    securityMutation.mutate({
      mfa_policy: selectedMfa,
      auto_accept_same_domain: autoAcceptSameDomain,
    })
  }

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-base font-display-bold text-gray-900">
          {m.admin_settings_security_title()}
        </h2>
      </div>
      <form onSubmit={handleSubmit} className="space-y-6">
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

            {settings?.primary_domain && (
              <div className="space-y-1">
                <Checkbox
                  id="settings-auto-accept"
                  checked={autoAcceptSameDomain}
                  disabled={securityMutation.isPending}
                  onChange={(event) => setAutoAcceptSameDomain(event.target.checked)}
                  label={m.admin_settings_auto_accept_label({ domain: settings.primary_domain })}
                />
                <p className="pl-7 text-xs text-gray-400">
                  {autoAcceptSameDomain
                    ? m.admin_settings_auto_accept_hint_on()
                    : m.admin_settings_auto_accept_hint_off()}
                </p>
              </div>
            )}
            {securityMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
            )}
            <div className="pt-2">
              <Button
                type="submit"
                disabled={securityMutation.isPending || savedSecurity || !securityDirty}
              >
                {savedSecurity
                  ? m.admin_settings_saved()
                  : securityMutation.isPending
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
