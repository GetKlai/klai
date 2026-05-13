import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import type { OrgGroup, OrgUser, Step, WizardData, WizardErrorKey } from './new._types'

export function buildCreateKnowledgeBasePayload(data: WizardData) {
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
            ...data.initialGroups.map((g) => ({
              type: 'group',
              id: String(g.id),
              role: g.role,
            })),
            ...data.initialUsers.map((u) => ({
              type: 'user',
              id: u.id,
              role: u.role,
            })),
          ]
        : undefined,
  }
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
    mutationFn: async () => {
      return apiFetch<{ slug: string }>(`/api/app/knowledge-bases`, {
        method: 'POST',
        body: JSON.stringify(buildCreateKnowledgeBasePayload(data)),
      })
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug: result.slug } })
    },
    onError: (err: Error) => {
      onErrorKey(err instanceof ApiError && err.status === 409 ? 'conflict' : 'generic')
    },
  })
}
