/**
 * Portal-api HTTP client — BFF edition (SPEC-AUTH-008 Phase B).
 *
 * All requests are same-origin and authenticated via the `__Secure-klai_session`
 * HttpOnly cookie that portal-api sets after the OIDC callback. State-changing
 * requests automatically carry the matching CSRF token from the readable
 * `__Secure-klai_csrf` cookie.
 *
 * The `token` parameter accepted by `apiFetch` is retained for migration
 * compatibility — it is ignored. The backend's session-aware `bearer` shim
 * authenticates from the session cookie regardless. Callers can pass
 * `undefined` (or any string) and the request will still work.
 *
 * `ApiError` extends the typed `FetchError` from `lib/fetch-errors` so the
 * same `isRetryable` / `friendlyErrorKey` helpers that the callback +
 * provisioning routes use also apply to every apiFetch response. `UnauthorizedError`
 * is re-exported for callers that want to check for token-rot explicitly.
 */

import { API_BASE } from '@/lib/api'
import { readCsrfCookie } from '@/lib/auth'
import { FetchError, UnauthorizedError } from '@/lib/fetch-errors'
import { authLogger } from '@/lib/logger'

export { UnauthorizedError } from '@/lib/fetch-errors'

/**
 * Non-OK HTTP response from portal-api. Extends FetchError so callers can use
 * the shared transient-vs-permanent classification while still reading the
 * server-supplied `detail` string (typically for 409 Conflict handling).
 */
export class ApiError extends FetchError {
  readonly detail: string

  constructor(status: number, detail: string) {
    super(status)
    this.name = 'ApiError'
    this.message = `${status}: ${detail}`
    this.detail = detail
  }
}

interface ApiFetchOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
}

const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

// ---------------------------------------------------------------------------
// Migration compat — some callers still pass a TokenRefresher to auth.tsx.
// With the BFF the backend handles refresh server-side, so this is a no-op
// kept alive until the last caller is removed.
// ---------------------------------------------------------------------------

type TokenRefresher = () => Promise<string | null>

export function registerTokenRefresher(_refresher: TokenRefresher): void {
  // No-op in BFF mode — sessions are refreshed by portal-api middleware.
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function doFetch<T>(path: string, options: ApiFetchOptions): Promise<T> {
  const { headers: extraHeaders, method = 'GET', ...rest } = options

  const headers: Record<string, string> = { ...extraHeaders }
  if (rest.body && typeof rest.body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  if (MUTATING_METHODS.has(method.toUpperCase())) {
    const csrf = readCsrfCookie()
    if (csrf) headers['X-CSRF-Token'] = csrf
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: 'include',
    headers,
    ...rest,
  })

  if (res.status === 401) {
    throw new UnauthorizedError()
  }
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = (await res.json()) as { detail?: string | object[] }
      if (body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/**
 * Authenticated fetch against portal-api.
 *
 * Signature kept compatible with the pre-BFF version — the `_legacyToken`
 * slot is ignored. Callers can write either `apiFetch(path, token, options)`
 * or `apiFetch(path, undefined, options)`.
 */
export async function apiFetch<T>(
  path: string,
  _legacyToken?: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  try {
    return await doFetch<T>(path, options)
  } catch (err) {
    if (err instanceof UnauthorizedError) {
      authLogger.info('apiFetch received 401 — session expired or missing', { path })
    }
    throw err
  }
}
