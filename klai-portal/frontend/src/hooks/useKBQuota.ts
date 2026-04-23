/**
 * useKBQuota
 *
 * SPEC-PORTAL-UNIFY-KB-001 — centralized quota state so components do not
 * duplicate the limit-check logic.
 *
 * Rules (from plan_limits.py):
 *  - core / professional: max 5 personal KBs per user, max 20 items per KB.
 *  - complete: unlimited.
 *
 * The "limited" tier is detected via capabilities: if the user lacks
 * "kb.connectors" they are on a limited plan (core or professional).
 * Admins are always treated as unlimited.
 *
 * Quota limits match PLAN_LIMITS in backend/app/core/plan_limits.py.
 */
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import { useCurrentUser } from '@/hooks/useCurrentUser'

const MAX_PERSONAL_KBS = 5
const MAX_ITEMS_PER_KB = 20

interface KnowledgeBase {
  id: number
  slug: string
  owner_type: string
  owner_user_id: string | null
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

interface KBStats {
  docs_count: number | null
}

interface UseKBQuotaResult {
  /** True when the user can create another personal KB. */
  canCreateKB: boolean
  /** True when more items can be added to the given kbSlug. */
  canAddItem: boolean
  /** Human-readable reason for the constraint (use as tooltip copy key). */
  reason?: 'kb_count' | 'kb_items'
  /** True while quota data is still loading. */
  isLoading: boolean
}

/**
 * Returns quota state for KB creation and item upload.
 *
 * @param kbSlug – Optional. When provided, also checks item-level quota for
 *   that specific KB.
 */
export function useKBQuota(kbSlug?: string): UseKBQuotaResult {
  const auth = useAuth()
  const { user } = useCurrentUser()
  const myUserId = auth.user?.profile?.sub

  // Users with "kb.connectors" capability are on the complete plan (unlimited).
  // Admins are always unlimited via hasCapability.
  const isLimited = user ? !user.hasCapability('kb.connectors') : false

  const { data: kbsData, isLoading: kbsLoading } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated && isLimited,
    staleTime: 60_000,
  })

  const { data: statsData, isLoading: statsLoading } = useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`),
    enabled: auth.isAuthenticated && isLimited && !!kbSlug,
    staleTime: 60_000,
  })

  // Unlimited plan — no restrictions.
  if (!isLimited) {
    return { canCreateKB: true, canAddItem: true, isLoading: false }
  }

  const isLoading = kbsLoading || (!!kbSlug && statsLoading)

  // Count personal KBs owned by this user.
  const personalKBs = (kbsData?.knowledge_bases ?? []).filter(
    (kb) => kb.owner_type === 'user' && kb.owner_user_id === myUserId,
  )
  const personalKBCount = personalKBs.length

  const canCreateKB = personalKBCount < MAX_PERSONAL_KBS
  const itemCount = statsData?.docs_count ?? 0
  const canAddItem = itemCount < MAX_ITEMS_PER_KB

  const reason: 'kb_count' | 'kb_items' | undefined = !canCreateKB
    ? 'kb_count'
    : !canAddItem
      ? 'kb_items'
      : undefined

  return { canCreateKB, canAddItem, reason, isLoading }
}
