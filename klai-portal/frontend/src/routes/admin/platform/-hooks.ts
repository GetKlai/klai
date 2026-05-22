// SPEC-PLATFORM-ADMIN-001 — data hooks for the cross-tenant console.
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import type {
  PlatformStats,
  PlatformUser,
  PlatformOrg,
  PlatformBot,
  PlatformChatError,
  PlatformOrgDetail,
  PlatformKB,
  PlatformTemplate,
  CreateTenantPayload,
  CreateTenantResult,
} from './-types'

export function usePlatformStats() {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-stats'],
    queryFn: async () => apiFetch<PlatformStats>('/api/admin/platform/stats'),
    enabled: auth.isAuthenticated,
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

// Live liveness-probe against portal-api's /api/health (unauthenticated allowlist
// endpoint). Raw fetch — not apiFetch — so a transient 5xx surfaces as a plain
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
