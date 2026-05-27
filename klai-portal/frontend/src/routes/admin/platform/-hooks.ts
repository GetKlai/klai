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
  PlatformFeedbackSubmission,
  PlatformOrgDetail,
  PlatformKB,
  PlatformTemplate,
  CreateTenantPayload,
  CreateTenantResult,
} from './-types'

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

export function usePlatformFeedbackItems(search: string) {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-feedback-items', search],
    queryFn: async () =>
      apiFetch<PlatformFeedbackItem[]>(
        `/api/admin/platform/feedback/items?limit=25${
          search ? `&search=${encodeURIComponent(search)}` : ''
        }`,
      ),
    enabled: auth.isAuthenticated,
  })
}

function useFeedbackMutation() {
  const qc = useQueryClient()
  return {
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['platform-feedback-submissions'] })
      void qc.invalidateQueries({ queryKey: ['platform-feedback-items'] })
    },
  }
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
    }) =>
      apiFetch<PlatformFeedbackActionResult>(
        `/api/admin/platform/feedback/submissions/${vars.submissionId}/links`,
        {
          method: 'POST',
          body: JSON.stringify({
            item_id: vars.item_id,
            link_type: vars.link_type || 'evidence',
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
