/**
 * User service — wraps all /api/admin/users fetch calls.
 * Use these functions inside useQuery/useMutation queryFn, not raw fetch.
 *
 * New code should follow this pattern. Existing components with inline fetch
 * can be migrated here when they are touched for other reasons.
 */
import { apiFetch } from '@/lib/api'

export interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  preferred_language: 'nl' | 'en'
  role: 'admin' | 'member'
  status: 'active' | 'suspended' | 'offboarded'
  invite_pending: boolean
}

export interface UserGroup {
  id: number
  name: string
  is_system: boolean
}

export async function fetchUsers(token: string): Promise<{ users: User[] }> {
  return apiFetch('/api/admin/users', token)
}

export async function patchUser(
  token: string,
  userId: string,
  data: { first_name?: string; last_name?: string; preferred_language?: string },
): Promise<void> {
  return apiFetch(`/api/admin/users/${userId}`, token, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export async function fetchUserGroups(token: string, userId: string): Promise<{ groups: UserGroup[] }> {
  return apiFetch(`/api/admin/users/${userId}/groups`, token)
}

export async function addUserToGroup(token: string, groupId: number, userId: string): Promise<void> {
  return apiFetch(`/api/admin/groups/${groupId}/members`, token, {
    method: 'POST',
    body: JSON.stringify({ zitadel_user_id: userId }),
  })
}

export async function removeUserFromGroup(token: string, groupId: number, userId: string): Promise<void> {
  return apiFetch(`/api/admin/groups/${groupId}/members/${userId}`, token, {
    method: 'DELETE',
  })
}
