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
  // SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 3: powers Phase 4 tile-filter on
  // /admin/index.tsx and the Uitbreidingen-sectie on /admin/settings.
  is_platform_admin?: boolean
  platform_unlocked_features?: string[]
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

/**
 * SPEC-INFRA-TENANT-DELETE-003 Bug 3 - provisioning failure detection.
 *
 * The state machine writes any of: `failed_rollback_pending`,
 * `failed_rollback_complete`, or `failed_deprovisioning` for terminal
 * failures. The literal `'failed'` value is NOT emitted by the backend
 * - old polling code that checked `status === 'failed'` never matched
 * and silently timed out after 5 minutes instead of failing fast.
 *
 * Treat any state with the `failed` prefix as a fatal provisioning state.
 * The `deprovisioning` state is intentionally NOT included - that's an
 * active in-progress lifecycle event with its own handling
 * (`tenant_deleting` 403 in `_get_caller_org`).
 */
export function isFailedProvisioningStatus(status: string | undefined): boolean {
  return typeof status === 'string' && status.startsWith('failed')
}

/** SPEC-INFRA-TENANT-DELETE-003 Bug 3 - match either pending or terminal-failure. */
export function isInFlightProvisioningStatus(status: string | undefined): boolean {
  return status === 'pending' || isFailedProvisioningStatus(status)
}
