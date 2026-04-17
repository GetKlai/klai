import { API_BASE } from '@/lib/api'
import { authLogger } from '@/lib/logger'

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

interface ApiFetchOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
}

// ---------------------------------------------------------------------------
// Token refresh coordination
//
// A single in-flight refresh is shared across all concurrent 401 retries.
// This prevents N parallel requests from each triggering their own
// signinSilent(), which would hammer the OIDC provider.
// ---------------------------------------------------------------------------

type TokenRefresher = () => Promise<string | null>

let _tokenRefresher: TokenRefresher | null = null
let _refreshPromise: Promise<string | null> | null = null

/**
 * Register the OIDC token refresher. Called once from KlaiAuthProvider.
 * The callback should call signinSilent() and return the new access_token,
 * or null if renewal failed.
 */
export function registerTokenRefresher(refresher: TokenRefresher): void {
  _tokenRefresher = refresher
}

/**
 * Coalesce concurrent refresh attempts into a single signinSilent() call.
 */
function refreshToken(): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise
  if (!_tokenRefresher) return Promise.resolve(null)

  _refreshPromise = _tokenRefresher()
    .catch((err) => {
      authLogger.warn('Silent token refresh failed', { error: err })
      return null
    })
    .finally(() => {
      _refreshPromise = null
    })

  return _refreshPromise
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function doFetch<T>(
  path: string,
  token: string | undefined,
  options: ApiFetchOptions,
): Promise<T> {
  const { headers: extraHeaders, ...rest } = options
  const headers: Record<string, string> = {
    ...extraHeaders,
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (rest.body && typeof rest.body === 'string') {
    headers['Content-Type'] ??= 'application/json'
  }

  const res = await fetch(`${API_BASE}${path}`, { headers, ...rest })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = (await res.json()) as { detail?: string | object[] }
      if (body.detail) {
        detail = typeof body.detail === 'string'
          ? body.detail
          : JSON.stringify(body.detail)
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
 * Fetch wrapper with automatic 401 retry-after-refresh.
 *
 * On a 401 response the function requests a fresh token via signinSilent()
 * and retries the request exactly once. Concurrent 401s share the same
 * refresh call to avoid hammering the OIDC provider.
 *
 * Non-mutating requests (GET, HEAD, OPTIONS) are always safe to retry.
 * Mutating requests are also retried because 401 means the server rejected
 * the request before processing it — the body was never applied.
 */
export async function apiFetch<T>(
  path: string,
  token: string | undefined,
  options: ApiFetchOptions = {},
): Promise<T> {
  try {
    return await doFetch<T>(path, token, options)
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401 || !token) throw err

    // Attempt a single silent refresh and retry
    const newToken = await refreshToken()
    if (!newToken || newToken === token) throw err

    authLogger.info('Retrying request after token refresh', { path })
    return doFetch<T>(path, newToken, options)
  }
}
