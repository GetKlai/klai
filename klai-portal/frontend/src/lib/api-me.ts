/**
 * Thin typed client for portal-api's /api/me endpoint (SPEC-AUTH-008).
 *
 * All requests are same-origin + cookie-authenticated.
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
 * Fetch /api/me via the BFF session cookie.
 *
 * Classifies failures as:
 *   - UnauthorizedError on 401 (session expired → reauth)
 *   - FetchError on any other non-OK status
 *   - TypeError pass-through on network failure
 *   - DOMException('AbortError') on signal abort
 */
export async function fetchMe(signal: AbortSignal): Promise<MeResponse> {
  const res = await fetch(`${API_BASE}/api/me`, {
    credentials: 'include',
    signal,
  })
  if (res.ok) return (await res.json()) as MeResponse
  if (res.status === 401) throw new UnauthorizedError()
  throw new FetchError(res.status)
}
