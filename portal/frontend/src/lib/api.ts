export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''
export const CONNECTOR_API_BASE = import.meta.env.VITE_CONNECTOR_API_BASE_URL ?? API_BASE

/**
 * Authenticated fetch helper. Single source for base URL + auth header.
 * Use this in all service functions under src/services/.
 *
 * @example
 * export async function fetchUsers(token: string) {
 *   return apiFetch<{ users: User[] }>('/api/admin/users', token)
 * }
 */
export async function apiFetch<T = unknown>(
  path: string,
  token: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...options?.headers,
    },
  })
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`)
  return res.json() as Promise<T>
}
