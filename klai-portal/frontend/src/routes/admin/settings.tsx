import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { apiFetch } from '@/lib/apiFetch'
import { Checkbox } from '@/components/ui/checkbox'
import * as m from '@/paraglide/messages'
import { adminLogger } from '@/lib/logger'

export const Route = createFileRoute('/admin/settings')({
  component: AdminSettingsPage,
})

type OrgSettings = {
  name: string
  default_language: 'nl' | 'en'
  mfa_policy: 'optional' | 'recommended' | 'required'
  auto_accept_same_domain: boolean
  primary_domain: string | null
}

function AdminSettingsPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [savedLang, setSavedLang] = useState(false)
  const [savedMfa, setSavedMfa] = useState(false)

  const { data: settings, isLoading, error } = useQuery({
    queryKey: ['admin-settings'],
    queryFn: async () => apiFetch<OrgSettings>(`/api/admin/settings`),
    enabled: auth.isAuthenticated,
  })

  async function patchSettings(payload: Partial<Pick<OrgSettings, 'default_language' | 'mfa_policy' | 'auto_accept_same_domain'>>) {
    return apiFetch<OrgSettings>(`/api/admin/settings`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  const langMutation = useMutation({
    mutationFn: (lang: 'nl' | 'en') => patchSettings({ default_language: lang }),
    onSuccess: (_data, lang) => { adminLogger.info('Default language changed', { language: lang }); setSavedLang(true); setTimeout(() => setSavedLang(false), 2500) },
  })

  const mfaMutation = useMutation({
    mutationFn: (policy: 'optional' | 'recommended' | 'required') => patchSettings({ mfa_policy: policy }),
    onSuccess: (_data, policy) => { adminLogger.info('MFA policy changed', { policy }); setSavedMfa(true); setTimeout(() => setSavedMfa(false), 2500) },
  })

  // R5: auto_accept toggle mutation — immediate PATCH on change, no separate save button
  const autoAcceptMutation = useMutation({
    mutationFn: (value: boolean) => patchSettings({ auto_accept_same_domain: value }),
    onSuccess: (data, value) => {
      adminLogger.info('Auto-accept same domain changed', { auto_accept_same_domain: value })
      queryClient.setQueryData(['admin-settings'], data)
    },
  })

  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>('nl')
  const [selectedMfa, setSelectedMfa] = useState<'optional' | 'recommended' | 'required'>('optional')

  useEffect(() => {
    if (settings) {
      setSelectedLang(settings.default_language)
      setSelectedMfa(settings.mfa_policy ?? 'optional')
    }
  }, [settings])

  // SPEC-PORTAL-PROFILES-001 P3.6: Add-on toggles.
  // UX pattern: ticking a checkbox stages the change; user clicks Save to commit.
  // Consistent with Language and MFA sections above.
  const [addons, setAddons] = useState<string[]>([])
  const [savedAddons, setSavedAddons] = useState(false)

  const { data: addonsData, isLoading: addonsLoading, error: addonsQueryError } = useQuery({
    queryKey: ['admin-enabled-addons'],
    queryFn: async () => apiFetch<{ enabled_addons: string[] }>('/api/admin/settings/addons'),
    enabled: auth.isAuthenticated,
  })

  useEffect(() => {
    if (addonsData) {
      setAddons(addonsData.enabled_addons ?? [])
    }
  }, [addonsData])

  const addonsMutation = useMutation({
    mutationFn: (next: string[]) =>
      apiFetch('/api/admin/settings/addons', {
        method: 'PATCH',
        body: JSON.stringify({ enabled_addons: next }),
      }),
    onSuccess: (_data, next) => {
      adminLogger.info('Add-ons updated', { enabled_addons: next })
      // SPEC-PORTAL-RBAC-001 REQ-12: keep the addons-list cache in sync with
      // the saved state so addonsDirty flips back to false and the Save
      // button disables straight away.
      queryClient.setQueryData(['admin-enabled-addons'], { enabled_addons: next })
      // The sidebar in /app reads `user.products` from useCurrentUser()
      // (queryKey ['current-user']). user.products is derived server-side
      // from (role, plan, enabled_addons), so flipping an add-on toggle
      // invalidates that view too. Without this, /app keeps the stale
      // products list until the next hard refresh and the toggled-off
      // add-on lingers in the sidebar.
      void queryClient.invalidateQueries({ queryKey: ['current-user'] })
      setSavedAddons(true)
      setTimeout(() => setSavedAddons(false), 2500)
    },
  })

  function toggleStaged(addon: string, enabled: boolean) {
    setAddons((prev) =>
      enabled ? [...new Set([...prev, addon])] : prev.filter((a) => a !== addon),
    )
  }

  const addonsDirty =
    addonsData != null &&
    JSON.stringify([...addons].sort()) !== JSON.stringify([...(addonsData.enabled_addons ?? [])].sort())

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6" data-help-id="admin-settings-general">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_settings_heading()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.admin_settings_subtitle()}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_settings_language_title()}</CardTitle>
          <CardDescription>
            {m.admin_settings_language_description()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_users_loading()}</p>
          ) : error ? (
            <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="settings-language">
                  {m.admin_settings_language_label()}
                </Label>
                <Select
                  id="settings-language"
                  value={selectedLang}
                  onChange={(e) => setSelectedLang(e.target.value as 'nl' | 'en')}
                  className="max-w-xs"
                >
                  <option value="nl">{m.admin_settings_language_nl()}</option>
                  <option value="en">{m.admin_settings_language_en()}</option>
                </Select>
              </div>
              {langMutation.error && (
                <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
              )}
              <Button
                onClick={() => langMutation.mutate(selectedLang)}
                disabled={langMutation.isPending || savedLang}
              >
                {savedLang
                  ? m.admin_settings_saved()
                  : langMutation.isPending
                    ? m.admin_settings_saving()
                    : m.admin_settings_save()}
              </Button>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_settings_security_title()}</CardTitle>
          <CardDescription>
            {m.admin_settings_security_description()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {isLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_users_loading()}</p>
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
                  onChange={(e) => setSelectedMfa(e.target.value as 'optional' | 'recommended' | 'required')}
                  className="max-w-xs"
                >
                  <option value="optional">{m.admin_settings_mfa_optional()}</option>
                  <option value="recommended">{m.admin_settings_mfa_recommended()}</option>
                  <option value="required">{m.admin_settings_mfa_required()}</option>
                </Select>
                <p className="text-xs text-[var(--color-muted-foreground)]">
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

              {/* R5: auto_accept_same_domain toggle — only shown when primary_domain is set */}
              {settings?.primary_domain && (
                <div className="border-t pt-4 space-y-1.5">
                  <div className="flex items-center justify-between gap-4">
                    <div className="space-y-0.5">
                      <Label htmlFor="settings-auto-accept" className="cursor-pointer">
                        {m.admin_settings_auto_accept_label({ domain: settings.primary_domain })}
                      </Label>
                      <p className="text-xs text-[var(--color-muted-foreground)]">
                        {settings.auto_accept_same_domain
                          ? m.admin_settings_auto_accept_hint_on()
                          : m.admin_settings_auto_accept_hint_off()}
                      </p>
                    </div>
                    {/* Rounded-full toggle (C5.4) */}
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

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_settings_org_title()}</CardTitle>
          <CardDescription>
            {m.admin_settings_org_description()}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_settings_placeholder()}</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{m.admin_settings_addons_title()}</CardTitle>
          <CardDescription>
            {m.admin_settings_addons_description()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {addonsLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_users_loading()}</p>
          ) : addonsQueryError ? (
            <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
          ) : (
            <>
              <div className="space-y-3">
                <Checkbox
                  checked={addons.includes('scribe')}
                  onChange={(e) => toggleStaged('scribe', e.target.checked)}
                  disabled={addonsMutation.isPending}
                  label={m.admin_settings_addon_scribe()}
                />
                <Checkbox
                  checked={addons.includes('docs')}
                  onChange={(e) => toggleStaged('docs', e.target.checked)}
                  disabled={addonsMutation.isPending}
                  label={m.admin_settings_addon_docs()}
                />
              </div>
              {addonsMutation.error && (
                <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
              )}
              <Button
                onClick={() => addonsMutation.mutate(addons)}
                disabled={addonsMutation.isPending || savedAddons || !addonsDirty}
              >
                {savedAddons
                  ? m.admin_settings_saved()
                  : addonsMutation.isPending
                    ? m.admin_settings_saving()
                    : m.admin_settings_save()}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
