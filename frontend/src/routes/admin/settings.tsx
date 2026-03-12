import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { API_BASE } from '@/lib/api'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/settings')({
  component: AdminSettingsPage,
})

function AdminSettingsPage() {
  const auth = useAuth()
  const token = auth.user?.access_token

  const [savedLang, setSavedLang] = useState(false)
  const [savedMfa, setSavedMfa] = useState(false)

  const { data: settings, isLoading, error } = useQuery({
    queryKey: ['admin-settings', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/settings`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_settings_error_fetch())
      return res.json() as Promise<{ name: string; default_language: 'nl' | 'en'; mfa_policy: 'optional' | 'recommended' | 'required' }>
    },
    enabled: !!token,
  })

  async function patchSettings(payload: { default_language?: 'nl' | 'en'; mfa_policy?: 'optional' | 'recommended' | 'required' }) {
    const res = await fetch(`${API_BASE}/api/admin/settings`, {
      method: 'PATCH',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) throw new Error(m.admin_settings_error_save())
    return res.json()
  }

  const langMutation = useMutation({
    mutationFn: (lang: 'nl' | 'en') => patchSettings({ default_language: lang }),
    onSuccess: () => { setSavedLang(true); setTimeout(() => setSavedLang(false), 2500) },
  })

  const mfaMutation = useMutation({
    mutationFn: (policy: 'optional' | 'recommended' | 'required') => patchSettings({ mfa_policy: policy }),
    onSuccess: () => { setSavedMfa(true); setTimeout(() => setSavedMfa(false), 2500) },
  })

  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>('nl')
  const [selectedMfa, setSelectedMfa] = useState<'optional' | 'recommended' | 'required'>('optional')

  useEffect(() => {
    if (settings) {
      setSelectedLang(settings.default_language)
      setSelectedMfa(settings.mfa_policy ?? 'optional')
    }
  }, [settings])

  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
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
    </div>
  )
}
