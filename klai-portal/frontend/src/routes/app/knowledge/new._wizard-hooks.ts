import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import type {
  MemberRole,
  OrgGroup,
  OrgUser,
  Step,
  WizardData,
  WizardErrorKey,
} from './new._types'

interface CreateKnowledgeBasePayload {
  name: string
  slug: string
  description?: string
  visibility: 'public' | 'internal' | 'private'
  owner_type: WizardData['ownerType']
  default_org_role: MemberRole | null
  initial_members?:
    | Array<{
        type: 'group' | 'user'
        id: string
        role: MemberRole
      }>
    | undefined
}

export function buildCreateKnowledgeBasePayload(
  data: WizardData
): CreateKnowledgeBasePayload {
  const visibility =
    data.visibilityMode === 'public'
      ? 'public'
      : data.visibilityMode === 'org'
        ? 'internal'
        : 'private'
  const defaultOrgRole =
    data.visibilityMode === 'restricted'
      ? null
      : data.allowContribute
        ? 'contributor'
        : 'viewer'

  return {
    name: data.name,
    slug: data.slug,
    description: data.description || undefined,
    visibility,
    owner_type: data.ownerType,
    default_org_role: defaultOrgRole,
    initial_members:
      data.ownerType === 'org'
        ? [
            ...data.initialGroups.map((group) => ({
              type: 'group' as const,
              id: String(group.id),
              role: group.role,
            })),
            ...data.initialUsers.map((user) => ({
              type: 'user' as const,
              id: user.id,
              role: user.role,
            })),
          ]
        : undefined,
  }
}

/** Structured `detail.error_code` values raised by app/services/kb_quota.py. */
const QUOTA_ERROR_KEYS: Record<string, WizardErrorKey> = {
  kb_quota_org_kb_not_allowed: 'org_not_allowed',
  kb_quota_personal_kb_exceeded: 'personal_quota',
}

function readErrorCode(detail: string): string | undefined {
  try {
    const parsed = JSON.parse(detail) as { error_code?: unknown }
    return typeof parsed.error_code === 'string' ? parsed.error_code : undefined
  } catch {
    // `detail` is a plain string, not the JSON-stringified detail object.
    return undefined
  }
}

/**
 * Force the personal scope when the backend would refuse an org KB. Derived
 * per render because `/api/me` resolves after mount. `visibilityMode` resets
 * too, otherwise a "public" pick from the org steps lands on a personal KB.
 */
export function resolveWizardOwnerScope(
  form: WizardData,
  canCreateOrgKB: boolean
): WizardData {
  if (canCreateOrgKB || form.ownerType === 'user') return form
  return { ...form, ownerType: 'user', visibilityMode: 'org' }
}

export function getCreateKnowledgeBaseErrorKey(error: Error): WizardErrorKey {
  if (!(error instanceof ApiError)) return 'generic'
  if (error.status === 409) return 'conflict'
  if (error.status === 403) {
    const code = readErrorCode(error.detail)
    if (code && QUOTA_ERROR_KEYS[code]) return QUOTA_ERROR_KEYS[code]
  }
  return 'generic'
}

export function useKnowledgeWizardMembers({
  isAuthenticated,
  ownerType,
  step,
}: {
  isAuthenticated: boolean
  ownerType: WizardData['ownerType']
  step: Step
}) {
  const enabled = isAuthenticated && ownerType === 'org' && step >= 3

  const { data: groupsData } = useQuery({
    queryKey: ['app-groups'],
    queryFn: () => apiFetch<{ groups: OrgGroup[] }>('/api/app/groups'),
    enabled,
  })

  const { data: usersData } = useQuery({
    queryKey: ['app-users'],
    queryFn: () => apiFetch<{ users: OrgUser[] }>('/api/app/users'),
    enabled,
  })

  return {
    groups: groupsData?.groups ?? [],
    users: usersData?.users ?? [],
  }
}

export function useCreateKnowledgeBaseMutation({
  data,
  onErrorKey,
}: {
  data: WizardData
  onErrorKey: (errorKey: WizardErrorKey) => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () =>
      apiFetch<{ slug: string }>('/api/app/knowledge-bases', {
        method: 'POST',
        body: JSON.stringify(buildCreateKnowledgeBasePayload(data)),
      }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug: result.slug } })
    },
    onError: (err: Error) => {
      onErrorKey(getCreateKnowledgeBaseErrorKey(err))
    },
  })
}
