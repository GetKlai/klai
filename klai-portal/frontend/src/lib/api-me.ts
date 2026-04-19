/**
 * Thin typed client for portal-api's /api/me endpoint.
 *
 * Shared by the post-login callback route and the provisioning poll so both
 * paths classify failures identically (via fetch-errors.ts) and stay in sync
 * with the backend contract.
 */

import { API_BASE } from '@/lib/api'
import { FetchError, UnauthorizedError } from '@/lib/fetch-errors'

export interface MeResponse {
  user_id?: string
  email?: string
  name?: string
  org_id?: string | null
  roles?: string[]
  workspace_url?: string | null
  provisioning_status?: string
  mfa_enrolled?: boolean
  mfa_policy?: string
  preferred_language?: 'nl' | 'en'
  portal_role?: string
  products?: string[]
  org_found?: boolean
}

/**
 * Fetch /api/me with the caller's access token, classifying failures as:
 *   - UnauthorizedError on 401 (token rot → reauth)
 *   - FetchError on any other non-OK status
 *   - TypeError pass-through on network failure
 *   - DOMException('AbortError') on signal abort
 */
export async function fetchMe(token: string, signal: AbortSignal): Promise<MeResponse> {
  const res = await fetch(`${API_BASE}/api/me`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  })
  if (res.ok) return (await res.json()) as MeResponse
  if (res.status === 401) throw new UnauthorizedError()
  throw new FetchError(res.status)
}
