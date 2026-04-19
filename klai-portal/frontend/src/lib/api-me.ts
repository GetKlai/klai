/**
 * Thin typed client for portal-api's /api/me endpoint (SPEC-AUTH-008 Phase B).
 *
 * All requests are same-origin + cookie-authenticated. The `_legacyToken`
 * parameter is kept for compatibility with callers written before the BFF
 * migration; it is ignored.
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
 *   - UnauthorizedError on 401 (token rot → reauth)
 *   - FetchError on any other non-OK status
 *   - TypeError pass-through on network failure
 *   - DOMException('AbortError') on signal abort
 */
export async function fetchMe(_legacyToken: string | undefined, signal: AbortSignal): Promise<MeResponse> {
  const res = await fetch(`${API_BASE}/api/me`, {
    credentials: 'include',
    signal,
  })
  if (res.ok) return (await res.json()) as MeResponse
  if (res.status === 401) throw new UnauthorizedError()
  throw new FetchError(res.status)
}
