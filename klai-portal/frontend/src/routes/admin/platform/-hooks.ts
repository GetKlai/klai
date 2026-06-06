// SPEC-PLATFORM-ADMIN-001 - data hooks for the cross-tenant console.
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import type {
  PlatformStats,
  PlatformUser,
  PlatformOrg,
  PlatformBot,
  PlatformChatError,
  PlatformFeedbackActionResult,
  PlatformFeedbackItem,
  PlatformFeedbackItemDetail,
  PlatformFeedbackResolveResult,
  PlatformFeedbackSubmission,
  PlatformMessageThread,
  PlatformMessageThreadDetail,
  PlatformOrgDetail,
  PlatformUnlocksResponse,
  PlatformKB,
  PlatformSubdomainItem,
  PlatformTemplate,
  CreateTenantPayload,
  CreateTenantResult,
} from './-types'

export function usePlatformSubdomains(enabled = true) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-subdomains'],
    queryFn: async () => apiFetch<PlatformSubdomainItem[]>('/api/admin/platform/subdomains'),
    // Liveness probes run server-side on every fetch (3s timeout × N items
    // in parallel = ~3s total). Cache briefly so flipping tabs does not
    // re-fire the whole probe storm.
    staleTime: 30_000,
    enabled: auth.isAuthenticated && enabled,
  })
}

export function usePlatformStats(enabled = true) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-stats'],
    queryFn: async () => apiFetch<PlatformStats>('/api/admin/platform/stats'),
    enabled: auth.isAuthenticated && enabled,
  })
}

export function usePlatformUsers(search: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-users', search],
    queryFn: async () =>
      apiFetch<PlatformUser[]>(
        `/api/admin/platform/users?limit=200${
          search ? `&search=${encodeURIComponent(search)}` : ''
        }`,
      ),
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformOrgs(search: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-orgs', search],
    queryFn: async () =>
      apiFetch<PlatformOrg[]>(
        `/api/admin/platform/organizations${
          search ? `?search=${encodeURIComponent(search)}` : ''
        }`,
      ),
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformBots(search: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-bots', search],
    queryFn: async () =>
      apiFetch<PlatformBot[]>(
        `/api/admin/platform/bots${
          search ? `?search=${encodeURIComponent(search)}` : ''
        }`,
      ),
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformOrgDetail(orgId: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-org-detail', orgId],
    queryFn: async () =>
      apiFetch<PlatformOrgDetail>(
        `/api/admin/platform/organizations/${orgId}`,
      ),
    enabled: auth.isAuthenticated && !!orgId,
  })
}

export function usePlatformUnlocks(slug: string | undefined) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-unlocks', slug],
    queryFn: async () =>
      apiFetch<PlatformUnlocksResponse>(
        `/api/admin/orgs/${encodeURIComponent(slug ?? '')}/platform-unlocks`,
      ),
    enabled: auth.isAuthenticated && !!slug,
  })
}

export function usePlatformUpdateUnlocks(orgId: string, slug: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (enabledFeatures: string[]) =>
      apiFetch<PlatformUnlocksResponse>(
        `/api/admin/orgs/${encodeURIComponent(slug)}/platform-unlocks`,
        {
          method: 'PATCH',
          body: JSON.stringify({ platform_unlocked_features: enabledFeatures }),
        },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-unlocks', slug] })
      void qc.invalidateQueries({ queryKey: ['platform-org-detail', orgId] })
      void qc.invalidateQueries({ queryKey: ['platform-orgs'] })
      void qc.invalidateQueries({ queryKey: ['me'] })
    },
  })
}

export function usePlatformKnowledgeBases(search: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-kbs', search],
    queryFn: async () =>
      apiFetch<PlatformKB[]>(
        `/api/admin/platform/knowledge-bases${
          search ? `?search=${encodeURIComponent(search)}` : ''
        }`,
      ),
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformTemplates(search: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-templates', search],
    queryFn: async () =>
      apiFetch<PlatformTemplate[]>(
        `/api/admin/platform/templates${
          search ? `?search=${encodeURIComponent(search)}` : ''
        }`,
      ),
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformChatErrors() {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-chat-errors'],
    queryFn: async () =>
      apiFetch<PlatformChatError[]>('/api/admin/platform/chat-errors?limit=100'),
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformMessageThreads(search: string, status = '') {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-message-threads', search, status],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: '200' })
      if (search) params.set('search', search)
      if (status) params.set('status', status)
      return apiFetch<PlatformMessageThread[]>(
        `/api/admin/platform/messages/threads?${params.toString()}`,
      )
    },
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformMessageThread(threadId: number | null) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-message-thread', threadId],
    queryFn: async () =>
      apiFetch<PlatformMessageThreadDetail>(
        `/api/admin/platform/messages/threads/${threadId}`,
      ),
    enabled: auth.isAuthenticated && threadId !== null,
  })
}

function usePlatformMessageMutation() {
  const qc = useQueryClient()
  return {
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-message-threads'] })
      void qc.invalidateQueries({ queryKey: ['platform-message-thread'] })
      void qc.invalidateQueries({ queryKey: ['platform-stats'] })
    },
  }
}

export function usePlatformCreateMessageThread() {
  const opts = usePlatformMessageMutation()
  return useMutation({
    mutationFn: async (body: {
      org_id: number
      user_ids: string[]
      subject: string
      body: string
      feedback_submission_id?: number | null
      feedback_item_id?: number | null
    }) =>
      apiFetch<PlatformMessageThreadDetail>('/api/admin/platform/messages/threads', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformReplyMessageThread() {
  const opts = usePlatformMessageMutation()
  return useMutation({
    mutationFn: async (vars: { threadId: number; body: string }) =>
      apiFetch<PlatformMessageThreadDetail>(
        `/api/admin/platform/messages/threads/${vars.threadId}/reply`,
        {
          method: 'POST',
          body: JSON.stringify({ body: vars.body }),
        },
      ),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformUpdateMessageThreadStatus() {
  const opts = usePlatformMessageMutation()
  return useMutation({
    mutationFn: async (vars: { threadId: number; status: 'open' | 'closed' }) =>
      apiFetch<PlatformMessageThreadDetail>(
        `/api/admin/platform/messages/threads/${vars.threadId}/status`,
        {
          method: 'PATCH',
          body: JSON.stringify({ status: vars.status }),
        },
      ),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackSubmissions(
  search: string,
  status: string,
  kind: string,
) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-feedback-submissions', search, status, kind],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: '200' })
      if (search) params.set('search', search)
      if (status) params.set('status', status)
      if (kind) params.set('kind', kind)
      return apiFetch<PlatformFeedbackSubmission[]>(
        `/api/admin/platform/feedback-submissions?${params.toString()}`,
      )
    },
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformFeedbackSubmission(submissionId: number | null) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-feedback-submission', submissionId],
    queryFn: async () =>
      apiFetch<PlatformFeedbackSubmission>(
        `/api/admin/platform/feedback/submissions/${submissionId}`,
      ),
    enabled: auth.isAuthenticated && submissionId !== null,
  })
}

export function usePlatformFeedbackItems(
  search: string,
  status = 'active',
  kind = 'all',
) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-feedback-items', search, status, kind],
    queryFn: async () => {
      const params = new URLSearchParams({
        limit: '100',
        status,
        kind,
      })
      if (search) params.set('search', search)
      return apiFetch<PlatformFeedbackItem[]>(
        `/api/admin/platform/feedback/items?${params.toString()}`,
      )
    },
    enabled: auth.isAuthenticated,
  })
}

export function usePlatformFeedbackItem(itemId: number | null) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-feedback-item', itemId],
    queryFn: async () =>
      apiFetch<PlatformFeedbackItemDetail>(
        `/api/admin/platform/feedback/items/${itemId}`,
      ),
    enabled: auth.isAuthenticated && itemId !== null,
  })
}

function useFeedbackMutation() {
  const qc = useQueryClient()
  return {
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-feedback-submissions'] })
      void qc.invalidateQueries({ queryKey: ['platform-feedback-items'] })
      void qc.invalidateQueries({ queryKey: ['platform-feedback-item'] })
    },
  }
}

export function usePlatformFeedbackUpdateSubmission() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (vars: {
      submissionId: number
      raw_text?: string | null
      status?: string
    }) => {
      const { submissionId, ...body } = vars
      return apiFetch<PlatformFeedbackActionResult>(
        `/api/admin/platform/feedback/submissions/${submissionId}`,
        {
          method: 'PATCH',
          body: JSON.stringify(body),
        },
      )
    },
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackDeleteSubmission() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (submissionId: number) =>
      apiFetch<void>(`/api/admin/platform/feedback/submissions/${submissionId}`, {
        method: 'DELETE',
      }),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackUpdateItem() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (vars: {
      itemId: number
      kind?: string
      title?: string
      summary?: string | null
      status?: string
      area?: string | null
    }) => {
      const { itemId, ...body } = vars
      return apiFetch<PlatformFeedbackItem>(
        `/api/admin/platform/feedback/items/${itemId}`,
        {
          method: 'PATCH',
          body: JSON.stringify(body),
        },
      )
    },
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackResolveItem() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (vars: {
      itemId: number
      resolution_summary: string
      channels: Array<'in_app' | 'email'>
      subject?: string | null
    }) => {
      const { itemId, ...body } = vars
      return apiFetch<PlatformFeedbackResolveResult>(
        `/api/admin/platform/feedback/items/${itemId}/resolve`,
        {
          method: 'POST',
          body: JSON.stringify(body),
        },
      )
    },
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackDeleteItem() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (itemId: number) =>
      apiFetch<void>(`/api/admin/platform/feedback/items/${itemId}`, {
        method: 'DELETE',
      }),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackDismiss() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (submissionId: number) =>
      apiFetch<PlatformFeedbackActionResult>(
        `/api/admin/platform/feedback/submissions/${submissionId}/dismiss`,
        { method: 'POST' },
      ),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackSupport() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (submissionId: number) =>
      apiFetch<PlatformFeedbackActionResult>(
        `/api/admin/platform/feedback/submissions/${submissionId}/support`,
        { method: 'POST' },
      ),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackCreateItem() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (vars: {
      submissionId: number
      kind: string
      title: string
      summary?: string | null
      area?: string | null
      link_type?: string
    }) =>
      apiFetch<PlatformFeedbackActionResult>(
        `/api/admin/platform/feedback/submissions/${vars.submissionId}/items`,
        {
          method: 'POST',
          body: JSON.stringify({
            kind: vars.kind,
            title: vars.title,
            summary: vars.summary || null,
            area: vars.area || null,
            link_type: vars.link_type || 'evidence',
          }),
        },
      ),
    onSuccess: opts.onSuccess,
  })
}

export function usePlatformFeedbackLinkItem() {
  const opts = useFeedbackMutation()
  return useMutation({
    mutationFn: async (vars: {
      submissionId: number
      item_id: number
      link_type?: string
      reopen_item?: boolean
    }) =>
      apiFetch<PlatformFeedbackActionResult>(
        `/api/admin/platform/feedback/submissions/${vars.submissionId}/links`,
        {
          method: 'POST',
          body: JSON.stringify({
            item_id: vars.item_id,
            link_type: vars.link_type || 'evidence',
            reopen_item: vars.reopen_item ?? false,
          }),
        },
      ),
    onSuccess: opts.onSuccess,
  })
}

// Live liveness-probe against portal-api's /api/health (unauthenticated allowlist
// endpoint). Raw fetch - not apiFetch - so a transient 5xx surfaces as a plain
// query error instead of triggering apiFetch's auth-refresh/redirect path.
export function usePortalHealth() {
  return useQuery({
    queryKey: ['platform-portal-health'],
    queryFn: async () => {
      const res = await fetch('/api/health', {
        headers: { Accept: 'application/json' },
      })
      if (!res.ok) throw new Error(`health ${res.status}`)
      return (await res.json()) as { status: string }
    },
    refetchInterval: 30_000,
    retry: false,
  })
}

// ── Create a brand-new tenant (fase C) ──────────────────────────

export function usePlatformCreateTenant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: CreateTenantPayload) =>
      apiFetch<CreateTenantResult>('/api/admin/platform/organizations', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-orgs'] })
      void qc.invalidateQueries({ queryKey: ['platform-stats'] })
    },
  })
}

// ── Cross-tenant write mutations (fase B+C) ─────────────────────

interface InvitePayload {
  email: string
  first_name: string
  last_name: string
  role: string
  preferred_language: 'nl' | 'en'
}

export function usePlatformInvite(orgId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: InvitePayload) =>
      apiFetch(`/api/admin/platform/organizations/${orgId}/users/invite`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-org-detail', orgId] })
      void qc.invalidateQueries({ queryKey: ['platform-users'] })
      void qc.invalidateQueries({ queryKey: ['platform-stats'] })
    },
  })
}

export function usePlatformChangeRole(orgId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { zid: string; role: string }) =>
      apiFetch(
        `/api/admin/platform/organizations/${orgId}/users/${vars.zid}/role`,
        { method: 'PATCH', body: JSON.stringify({ role: vars.role }) },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-org-detail', orgId] })
      void qc.invalidateQueries({ queryKey: ['platform-users'] })
    },
  })
}

export function usePlatformSuspend(orgId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { zid: string; reactivate: boolean }) =>
      apiFetch(
        `/api/admin/platform/organizations/${orgId}/users/${vars.zid}/${
          vars.reactivate ? 'reactivate' : 'suspend'
        }`,
        { method: 'POST' },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-org-detail', orgId] })
      void qc.invalidateQueries({ queryKey: ['platform-users'] })
    },
  })
}

// Hard-delete a user (everything): Zitadel identity, personal + solely-owned
// KBs, API keys + MCP tokens, and the portal_users row. Irreversible.
export function usePlatformDeleteUser(orgId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (zid: string) =>
      apiFetch(`/api/admin/platform/organizations/${orgId}/users/${zid}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-org-detail', orgId] })
      void qc.invalidateQueries({ queryKey: ['platform-users'] })
      void qc.invalidateQueries({ queryKey: ['platform-stats'] })
    },
  })
}

export function usePlatformRetryDeleteUser(orgId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (zid: string) =>
      apiFetch(
        `/api/admin/platform/organizations/${orgId}/users/${zid}/retry-delete`,
        { method: 'POST' },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-org-detail', orgId] })
      void qc.invalidateQueries({ queryKey: ['platform-users'] })
      void qc.invalidateQueries({ queryKey: ['platform-stats'] })
    },
  })
}

// Deprovision (delete) an entire tenant by slug - runs the 16-step
// orchestrator in the background. Returns 202 {status: "queued"}.
export function usePlatformDeprovisionTenant() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/admin/orgs/${slug}/deprovision`, { method: 'DELETE' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-orgs'] })
      void qc.invalidateQueries({ queryKey: ['platform-stats'] })
    },
  })
}
