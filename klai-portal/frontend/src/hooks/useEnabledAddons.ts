// SPEC-PORTAL-PROFILES-001 P3.8: Fetch enabled add-ons for the current tenant.
// Admin-only - only call from admin routes.
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'

interface AddonsResponse {
  enabled_addons: string[]
}

export function useEnabledAddons() {
  const auth = useAuth()
  const query = useQuery({
    queryKey: ['admin-enabled-addons'],
    queryFn: async () => apiFetch<AddonsResponse>('/api/admin/settings/addons'),
    enabled: auth.isAuthenticated,
    staleTime: 60 * 1000,
  })

  return {
    ...query,
    addons: query.data?.enabled_addons ?? [],
  }
}
