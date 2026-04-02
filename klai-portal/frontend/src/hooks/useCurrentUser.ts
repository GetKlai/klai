import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
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
  requires_2fa_setup?: boolean
}

export interface CurrentUser extends MeResponse {
  isAdmin: boolean
  isGroupAdmin: boolean
}

export function useCurrentUser() {
  const auth = useAuth()
  const token = auth.user?.access_token

  const query = useQuery({
    queryKey: ['current-user'],
    queryFn: async () => {
      const me = await apiFetch<MeResponse>('/api/me', token)
      return {
        ...me,
        isAdmin: me.roles?.some((r) => ADMIN_ROLES.includes(r)) ?? false,
        isGroupAdmin: me.portal_role === 'group-admin',
      } satisfies CurrentUser
    },
    enabled: !!token,
    staleTime: 5 * 60 * 1000,
  })

  return {
    ...query,
    user: query.data,
  }
}
