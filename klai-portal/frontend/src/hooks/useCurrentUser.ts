import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'

const ADMIN_ROLES = ['org:owner', 'org:admin']

interface MeResponse {
  user_id: string
  email: string
  name: string
  org_id: string | null
  roles: string[]
  workspace_url: string | null
  provisioning_status: string
  mfa_enrolled: boolean
  mfa_policy: string
  preferred_language: 'nl' | 'en'
  portal_role: string
  products: string[]
  // SPEC-PORTAL-UNIFY-KB-001: KB capability strings (e.g. "kb.connectors").
  // Empty array for core/professional; full set for complete.
  capabilities: string[]
  requires_2fa_setup?: boolean
}

export interface CurrentUser extends MeResponse {
  isAdmin: boolean
  isGroupAdmin: boolean
  /** Returns true when the user has the given KB capability OR is admin. */
  hasCapability: (cap: string) => boolean
}

export function useCurrentUser() {
  const auth = useAuth()
  const query = useQuery({
    queryKey: ['current-user'],
    queryFn: async () => {
      const me = await apiFetch<MeResponse>('/api/me')
      const isAdmin = me.roles?.some((r) => ADMIN_ROLES.includes(r)) ?? false
      return {
        ...me,
        // Ensure capabilities is always an array even if older backend omits it
        capabilities: me.capabilities ?? [],
        isAdmin,
        isGroupAdmin: me.portal_role === 'group-admin',
        hasCapability: (cap: string) => isAdmin || (me.capabilities ?? []).includes(cap),
      } satisfies CurrentUser
    },
    enabled: auth.isAuthenticated,
    staleTime: 5 * 60 * 1000,
  })

  return {
    ...query,
    user: query.data,
  }
}
