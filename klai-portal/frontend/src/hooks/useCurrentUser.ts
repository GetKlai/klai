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
  // SPEC-PORTAL-PROFILES-001 Phase 1: five-rung role ladder and effective capabilities.
  effective_role?: string
  effective_capabilities?: string[]
  // Whether POST /api/app/knowledge-bases will accept owner_type="org" for
  // this caller (profile AND plan must allow it). Deliberately not derivable
  // from `capabilities`: those are seat-derived and `hasCapability` short-
  // circuits to true for admins, while this gate reads the org plan.
  // Optional so a frontend bundle can outlive an older backend; absent is
  // treated as "not allowed" (fail closed).
  can_create_org_kbs?: boolean
  // Effective personal-KB cap, mirroring effective_kb_limits(role, plan).
  // `null` means unlimited; absent means an older backend, which callers treat
  // as the most restrictive paid tier.
  max_personal_kbs_per_user?: number | null
  requires_2fa_setup?: boolean
}

export interface CurrentUser extends MeResponse {
  isAdmin: boolean
  isGroupAdmin: boolean
  /**
   * True when the backend will accept `owner_type: "org"` on KB creation.
   * Fail-closed: an older backend that omits the field reads as false.
   */
  canCreateOrgKB: boolean
  /** Returns true when the user has the given KB capability OR is admin. */
  hasCapability: (cap: string) => boolean
}

export function deriveIsAdmin(me: Pick<MeResponse, 'roles' | 'portal_role'>): boolean {
  return me.portal_role === 'admin' || (me.roles?.some((r) => ADMIN_ROLES.includes(r)) ?? false)
}

export function useCurrentUser() {
  const auth = useAuth()
  const query = useQuery({
    queryKey: ['current-user'],
    queryFn: async () => {
      const me = await apiFetch<MeResponse>('/api/me')
      const isAdmin = deriveIsAdmin(me)
      return {
        ...me,
        // Ensure capabilities is always an array even if older backend omits it
        capabilities: me.capabilities ?? [],
        effective_capabilities: me.effective_capabilities ?? [],
        isAdmin,
        isGroupAdmin: me.portal_role === 'group-admin',
        canCreateOrgKB: me.can_create_org_kbs === true,
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
