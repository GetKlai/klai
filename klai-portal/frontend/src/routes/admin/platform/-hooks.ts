// SPEC-PLATFORM-ADMIN-001 — data hooks for the cross-tenant console.
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import type {
  PlatformStats,
  PlatformUser,
  PlatformOrg,
  PlatformBot,
  PlatformChatError,
  PlatformOrgDetail,
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

export function usePlatformChatErrors() {
  const auth = useAuth()
  return useQuery({
    queryKey: ['platform-chat-errors'],
    queryFn: async () =>
      apiFetch<PlatformChatError[]>('/api/admin/platform/chat-errors?limit=100'),
    enabled: auth.isAuthenticated,
  })
}
