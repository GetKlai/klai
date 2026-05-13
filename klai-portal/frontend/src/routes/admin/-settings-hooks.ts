import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import { fetchMe } from '@/lib/api-me'
import { useAuth } from '@/lib/auth'
import { adminLogger } from '@/lib/logger'

export type TelemetryLevel = 'off' | 'shadow' | 'full'

export type OrgSettings = {
  name: string
  default_language: 'nl' | 'en'
  mfa_policy: 'optional' | 'recommended' | 'required'
  auto_accept_same_domain: boolean
  primary_domain: string | null
  telemetry_level: TelemetryLevel
}

// SPEC-PORTAL-EXTENSIONS-UNIFY-001 — extensions API shape (i18n-clean:
// labels + descriptions live in Paraglide via lib/extensions-i18n.ts).
export type ExtensionItem = {
  key: string
  enabled: boolean
  requires_profile: string | null
  manageable_by_caller: boolean
}

export type ExtensionsResponse = {
  org_slug: string
  extensions: ExtensionItem[]
}

type PlatformUnlocksResponse = {
  slug: string
  platform_unlocked_features: string[]
}

export const adminSettingsQueryKey = ['admin-settings'] as const
export const adminExtensionsQueryKey = ['admin-extensions'] as const
export const meQueryKey = ['me'] as const

export function useAdminSettings() {
  const auth = useAuth()

  return useQuery({
    queryKey: adminSettingsQueryKey,
    queryFn: async () => apiFetch<OrgSettings>('/api/admin/settings'),
    enabled: auth.isAuthenticated,
  })
}

export function useAdminSettingsMe() {
  const auth = useAuth()

  return useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: auth.isAuthenticated,
  })
}

export function useAdminExtensions() {
  const auth = useAuth()

  return useQuery({
    queryKey: adminExtensionsQueryKey,
    queryFn: async () => apiFetch<ExtensionsResponse>('/api/admin/extensions'),
    enabled: auth.isAuthenticated,
  })
}

function patchSettings(
  payload: Partial<
    Pick<OrgSettings, 'default_language' | 'mfa_policy' | 'auto_accept_same_domain'>
  >,
) {
  return apiFetch<OrgSettings>('/api/admin/settings', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function useDefaultLanguageMutation(onSaved: () => void) {
  return useMutation({
    mutationFn: (lang: OrgSettings['default_language']) => patchSettings({ default_language: lang }),
    onSuccess: (_data, lang) => {
      adminLogger.info('Default language changed', { language: lang })
      onSaved()
    },
  })
}

export function useMfaPolicyMutation(onSaved: () => void) {
  return useMutation({
    mutationFn: (policy: OrgSettings['mfa_policy']) => patchSettings({ mfa_policy: policy }),
    onSuccess: (_data, policy) => {
      adminLogger.info('MFA policy changed', { policy })
      onSaved()
    },
  })
}

export function useAutoAcceptSameDomainMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (value: boolean) => patchSettings({ auto_accept_same_domain: value }),
    onSuccess: (data, value) => {
      adminLogger.info('Auto-accept same domain changed', { auto_accept_same_domain: value })
      queryClient.setQueryData(adminSettingsQueryKey, data)
    },
  })
}

export function useTelemetryLevelMutation(onSaved: () => void) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (level: TelemetryLevel) =>
      apiFetch<{ telemetry_level: TelemetryLevel }>('/api/orgs/me/telemetry-level', {
        method: 'POST',
        body: JSON.stringify({ level }),
      }),
    onSuccess: (data) => {
      adminLogger.info('Telemetry level changed', { telemetry_level: data.telemetry_level })
      queryClient.setQueryData(adminSettingsQueryKey, (prev: OrgSettings | undefined) =>
        prev ? { ...prev, telemetry_level: data.telemetry_level } : prev,
      )
      onSaved()
    },
  })
}

export function useExtensionsMutation(onSaved: () => void) {
  const queryClient = useQueryClient()

  return useMutation({
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
      void queryClient.invalidateQueries({ queryKey: adminExtensionsQueryKey })
      void queryClient.invalidateQueries({ queryKey: meQueryKey })
      onSaved()
    },
  })
}
