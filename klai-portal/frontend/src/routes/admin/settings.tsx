import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Checkbox } from '@/components/ui/checkbox'
import { apiFetch } from '@/lib/apiFetch'
import { fetchMe } from '@/lib/api-me'
import * as m from '@/paraglide/messages'
import { adminLogger } from '@/lib/logger'

export const Route = createFileRoute('/admin/settings')({
  component: AdminSettingsPage,
})

type TelemetryLevel = 'off' | 'shadow' | 'full'

type OrgSettings = {
  name: string
  default_language: 'nl' | 'en'
  mfa_policy: 'optional' | 'recommended' | 'required'
  auto_accept_same_domain: boolean
  primary_domain: string | null
  telemetry_level: TelemetryLevel
}

// SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 3/4 — extensions API shape.
type ExtensionItem = {
  key: string
  label: string
  description: string
  enabled: boolean
  requires_profile: string | null
  manageable_by_caller: boolean
}

type ExtensionsResponse = {
  org_slug: string
  extensions: ExtensionItem[]
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

  // SPEC-PRIVACY-QUERY-SHADOW-001 REQ-15: tenant self-service telemetry-level.
  // Posts to a dedicated tenant-scoped endpoint (different surface from the
  // operator-side /internal/admin/orgs/{org}/telemetry-level). The backend
  // shares the service-layer between both endpoints.
  const [selectedTelemetry, setSelectedTelemetry] = useState<TelemetryLevel>('shadow')
  const [savedTelemetry, setSavedTelemetry] = useState(false)

  useEffect(() => {
    if (settings?.telemetry_level) {
      setSelectedTelemetry(settings.telemetry_level)
    }
  }, [settings?.telemetry_level])

  const telemetryMutation = useMutation({
    mutationFn: async (level: TelemetryLevel) =>
      apiFetch<{ telemetry_level: TelemetryLevel }>(`/api/orgs/me/telemetry-level`, {
        method: 'POST',
        body: JSON.stringify({ level }),
      }),
    onSuccess: (data) => {
      adminLogger.info('Telemetry level changed', { telemetry_level: data.telemetry_level })
      // Mirror the saved state into the existing /api/admin/settings cache
      // so the read-only "Current setting" line updates without a refetch.
      queryClient.setQueryData(['admin-settings'], (prev: OrgSettings | undefined) =>
        prev ? { ...prev, telemetry_level: data.telemetry_level } : prev,
      )
      setSavedTelemetry(true)
      setTimeout(() => setSavedTelemetry(false), 2500)
    },
  })

  // ---------------------------------------------------------------------
  // SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 4: Uitbreidingen sectie.
  // Staged-toggle pattern, consistent with the Language / MFA / Telemetry
  // sections above: checkbox change updates local state only; Save commits.
  // Tenant-admin: checkboxes always disabled (read-only).
  // Platform-admin (Klai staff): checkboxes interactive on own org.
  // ---------------------------------------------------------------------
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: auth.isAuthenticated,
  })

  const { data: extensions, isLoading: extensionsLoading, error: extensionsError } = useQuery({
    queryKey: ['admin-extensions'],
    queryFn: async () => apiFetch<ExtensionsResponse>('/api/admin/extensions'),
    enabled: auth.isAuthenticated,
  })

  // Staged feature set + saved-flash state. Mirrors Language / MFA / Telemetry
  // sections — a Save button commits, not the checkbox change itself.
  const [stagedExtensions, setStagedExtensions] = useState<Set<string>>(new Set())
  const [savedExtensions, setSavedExtensions] = useState(false)

  useEffect(() => {
    if (extensions) {
      setStagedExtensions(
        new Set(extensions.extensions.filter((e) => e.enabled).map((e) => e.key)),
      )
    }
  }, [extensions])

  type PlatformUnlocksResponse = { slug: string; platform_unlocked_features: string[] }

  const extensionsMutation = useMutation({
    mutationFn: (next: { org_slug: string; enabled_features: string[] }) =>
      // Single write-path for extensions: PATCH /api/admin/orgs/{slug}/platform-unlocks.
      // Cleanup of SPEC-PORTAL-EXTENSIONS-UNIFY-001: the brief
      // /api/admin/extensions PATCH was retired so there is exactly one
      // audit trail in tenant_lifecycle_events.
      apiFetch<PlatformUnlocksResponse>(
        `/api/admin/orgs/${encodeURIComponent(next.org_slug)}/platform-unlocks`,
        {
          method: 'PATCH',
          body: JSON.stringify({ platform_unlocked_features: next.enabled_features }),
        },
      ),
    onSuccess: (data) => {
      adminLogger.info('Extensions updated', {
        org_slug: data.slug,
        enabled: data.platform_unlocked_features,
      })
      // Refresh the read-side payload + the /api/me tile-filter input.
      void queryClient.invalidateQueries({ queryKey: ['admin-extensions'] })
      void queryClient.invalidateQueries({ queryKey: ['me'] })
      setSavedExtensions(true)
      setTimeout(() => setSavedExtensions(false), 2500)
    },
  })

  function stageExtension(key: string, enabled: boolean) {
    setStagedExtensions((prev) => {
      const next = new Set(prev)
      if (enabled) next.add(key)
      else next.delete(key)
      return next
    })
  }

  const savedEnabled = new Set(
    extensions?.extensions.filter((e) => e.enabled).map((e) => e.key) ?? [],
  )
  const extensionsDirty =
    extensions != null &&
    (stagedExtensions.size !== savedEnabled.size ||
      [...stagedExtensions].some((k) => !savedEnabled.has(k)))

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6" data-help-id="admin-settings-general">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_settings_heading()}
        </h1>
        <p className="text-sm text-gray-400">
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
            <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
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
                  onChange={(e) => setSelectedMfa(e.target.value as 'optional' | 'recommended' | 'required')}
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

              {/* R5: auto_accept_same_domain toggle — only shown when primary_domain is set */}
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
          <p className="text-sm text-gray-400">{m.admin_settings_placeholder()}</p>
        </CardContent>
      </Card>
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

      {/* SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 4 — Uitbreidingen */}
      <Card>
        <CardHeader>
          <CardTitle>{m.admin_settings_extensions_title()}</CardTitle>
          <CardDescription>
            {(me?.is_platform_admin ?? false)
              ? m.admin_settings_extensions_description_platform()
              : m.admin_settings_extensions_description_tenant()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {extensionsLoading ? (
            <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
          ) : extensionsError ? (
            <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
          ) : !extensions ? null : (
            <>
              <ul className="divide-y divide-gray-200 border-t border-b border-gray-200">
                {extensions.extensions.map((item) => {
                  const staged = stagedExtensions.has(item.key)
                  return (
                    <li key={item.key} className="flex items-center justify-between gap-4 px-2 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-[15px] font-display text-gray-900">{item.label}</p>
                        <p className="text-xs text-gray-400 mt-0.5">{item.description}</p>
                      </div>
                      {item.manageable_by_caller ? (
                        <Checkbox
                          checked={staged}
                          onChange={(e) => stageExtension(item.key, e.target.checked)}
                          disabled={extensionsMutation.isPending}
                          label=""
                        />
                      ) : (
                        <span
                          className={[
                            'shrink-0 rounded-full px-3 py-0.5 text-xs font-medium',
                            item.enabled
                              ? 'bg-[var(--color-rl-cream)] text-[var(--color-rl-accent-dark)]'
                              : 'bg-gray-100 text-gray-400',
                          ].join(' ')}
                        >
                          {item.enabled
                            ? m.admin_settings_extensions_status_on()
                            : m.admin_settings_extensions_status_off()}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
              {!(me?.is_platform_admin ?? false) && (
                <p className="text-xs text-gray-400">
                  {m.admin_settings_extensions_managed_by_klai()}
                </p>
              )}
              {extensionsMutation.error && (
                <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_save()}</p>
              )}
              {(me?.is_platform_admin ?? false) && (
                <Button
                  onClick={() =>
                    extensionsMutation.mutate({
                      org_slug: extensions.org_slug,
                      enabled_features: [...stagedExtensions].sort(),
                    })
                  }
                  disabled={extensionsMutation.isPending || savedExtensions || !extensionsDirty}
                >
                  {savedExtensions
                    ? m.admin_settings_saved()
                    : extensionsMutation.isPending
                      ? m.admin_settings_saving()
                      : m.admin_settings_save()}
                </Button>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
