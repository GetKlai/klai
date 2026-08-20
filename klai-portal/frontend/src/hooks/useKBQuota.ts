/**
 * useKBQuota
 *
 * SPEC-PORTAL-UNIFY-KB-001 - centralized quota state so components do not
 * duplicate the limit-check logic.
 *
 * The personal-KB cap comes from `/api/me`, mirroring the
 * `effective_kb_limits(role, plan)` that `assert_can_create_personal_kb`
 * enforces. `null` means unlimited.
 *
 * The item cap still uses the old `kb.connectors` heuristic. That is wrong for
 * admins in the same way the KB cap was, but fixing it needs the KB's
 * `owner_type` (org KBs are exempt) and the ingest source count instead of
 * `docs_count` — out of scope here.
 */
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import { useCurrentUser, type CurrentUser } from '@/hooks/useCurrentUser'

/** Most restrictive paid tier — used when an older backend omits the cap. */
export const FALLBACK_MAX_PERSONAL_KBS = 5
const MAX_ITEMS_PER_KB = 20

/**
 * `null` = unlimited, and it must survive: `?? FALLBACK` would collapse it
 * with `undefined` and cap an unlimited caller. Only an absent field (older
 * backend) falls back.
 */
export function resolvePersonalKBLimit(
  user: Partial<Pick<CurrentUser, 'max_personal_kbs_per_user'>> | undefined,
): number | null {
  if (!user) return null
  if (user.max_personal_kbs_per_user === undefined) return FALLBACK_MAX_PERSONAL_KBS
  return user.max_personal_kbs_per_user
}

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

  const kbLimit = resolvePersonalKBLimit(user)

  // Unchanged heuristic — see the item-cap note in the module docstring.
  const isItemLimited = user ? !user.hasCapability('kb.connectors') : false

  const { data: kbsData, isLoading: kbsLoading } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated && kbLimit !== null,
    staleTime: 60_000,
  })

  const { data: statsData, isLoading: statsLoading } = useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`),
    enabled: auth.isAuthenticated && isItemLimited && !!kbSlug,
    staleTime: 60_000,
  })

  // Nothing capped (or user not resolved yet) - no restrictions to compute.
  if (kbLimit === null && !isItemLimited) {
    return { canCreateKB: true, canAddItem: true, isLoading: false }
  }

  const isLoading =
    (kbLimit !== null && kbsLoading) || (isItemLimited && !!kbSlug && statsLoading)

  // `undefined` when the list has not loaded (fetching or failed). Unknown is
  // not the same as exhausted: let the backend answer rather than claiming the
  // quota is full. `isLoading` is what blocks the submit button meanwhile.
  const personalKBCount = kbsData?.knowledge_bases.filter(
    (kb) => kb.owner_type === 'user' && kb.owner_user_id === myUserId,
  ).length

  const canCreateKB =
    kbLimit === null || personalKBCount === undefined || personalKBCount < kbLimit
  const itemCount = statsData?.docs_count ?? 0
  const canAddItem = !isItemLimited || itemCount < MAX_ITEMS_PER_KB

  const reason: 'kb_count' | 'kb_items' | undefined = !canCreateKB
    ? 'kb_count'
    : !canAddItem
      ? 'kb_items'
      : undefined

  return { canCreateKB, canAddItem, reason, isLoading }
}
