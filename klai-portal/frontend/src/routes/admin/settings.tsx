import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation } from '@tanstack/react-query'
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

function AdminSettingsPage() {
  const auth = useAuth()
  const [savedLang, setSavedLang] = useState(false)
  const [savedMfa, setSavedMfa] = useState(false)

  const { data: settings, isLoading, error } = useQuery({
    queryKey: ['admin-settings'],
    queryFn: async () => apiFetch<{ name: string; default_language: 'nl' | 'en'; mfa_policy: 'optional' | 'recommended' | 'required' }>(`/api/admin/settings`),
    enabled: auth.isAuthenticated,
  })

  async function patchSettings(payload: { default_language?: 'nl' | 'en'; mfa_policy?: 'optional' | 'recommended' | 'required' }) {
    return apiFetch(`/api/admin/settings`, {
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

  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>('nl')
  const [selectedMfa, setSelectedMfa] = useState<'optional' | 'recommended' | 'required'>('optional')

  useEffect(() => {
    if (settings) {
      setSelectedLang(settings.default_language)
      setSelectedMfa(settings.mfa_policy ?? 'optional')
    }
  }, [settings])

  // SPEC-PORTAL-PROFILES-001 P3.6: Add-on toggles
  const [addons, setAddons] = useState<string[]>([])
  const [savingAddons, setSavingAddons] = useState(false)
  const [savedAddons, setSavedAddons] = useState(false)
  const [addonsError, setAddonsError] = useState<string | null>(null)

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

  async function handleToggleAddon(addon: string, enabled: boolean) {
    const next = enabled ? [...new Set([...addons, addon])] : addons.filter((a) => a !== addon)
    setAddons(next)
    setSavingAddons(true)
    setAddonsError(null)
    try {
      await apiFetch('/api/admin/settings/addons', {
        method: 'PATCH',
        body: JSON.stringify({ enabled_addons: next }),
      })
      adminLogger.info('Add-ons updated', { enabled_addons: next })
      setSavedAddons(true)
      setTimeout(() => setSavedAddons(false), 2500)
    } catch (err) {
      setAddonsError(err instanceof Error ? err.message : m.admin_settings_error_save())
      // Revert optimistic update on error
      setAddons(addonsData?.enabled_addons ?? [])
    } finally {
      setSavingAddons(false)
    }
  }

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
        <CardContent className="space-y-4">
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
        <CardContent className="space-y-3">
          {addonsLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_users_loading()}</p>
          ) : addonsQueryError ? (
            <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
          ) : (
            <>
              <Checkbox
                checked={addons.includes('scribe')}
                onChange={(e) => void handleToggleAddon('scribe', e.target.checked)}
                disabled={savingAddons}
                label={m.admin_settings_addon_scribe()}
              />
              <Checkbox
                checked={addons.includes('docs')}
                onChange={(e) => void handleToggleAddon('docs', e.target.checked)}
                disabled={savingAddons}
                label={m.admin_settings_addon_docs()}
              />
              {addonsError && (
                <p className="text-sm text-[var(--color-destructive)]">{addonsError}</p>
              )}
              {savedAddons && (
                <p className="text-sm text-[var(--color-accent)]">{m.admin_settings_saved()}</p>
              )}
              {savingAddons && (
                <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_settings_saving()}</p>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
