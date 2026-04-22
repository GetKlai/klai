/**
 * Portal-api HTTP client — BFF edition (SPEC-AUTH-008).
 *
 * All requests are same-origin and authenticated via the `__Secure-klai_session`
 * HttpOnly cookie that portal-api sets after the OIDC callback. State-changing
 * requests automatically carry the matching CSRF token from the readable
 * `__Secure-klai_csrf` cookie.
 *
 * `ApiError` extends the typed `FetchError` from `lib/fetch-errors` so the
 * same `isRetryable` / `friendlyErrorKey` helpers used by the callback and
 * provisioning routes apply uniformly. `UnauthorizedError` is re-exported
 * for callers that want to detect session-rot explicitly.
 */

import { API_BASE } from '@/lib/api'
import { readCsrfCookie } from '@/lib/auth'
import { FetchError, UnauthorizedError } from '@/lib/fetch-errors'
import { authLogger } from '@/lib/logger'

export { UnauthorizedError } from '@/lib/fetch-errors'

/** One entry from a FastAPI / Pydantic validation error response body. */
export interface ValidationIssue {
  loc: (string | number)[]
  msg: string
  type: string
}

/**
 * Non-OK HTTP response from portal-api. Extends FetchError so callers can
 * use the shared transient-vs-permanent classification while still reading
 * the server-supplied `detail` string (typically for 409 Conflict handling).
 *
 * For 422 responses (FastAPI validation) the structured issue list is
 * preserved on `validationIssues`, and `message` is built as a
 * human-readable "field: reason; field: reason" summary — never the raw
 * JSON dump the frontend used to show.
 */
export class ApiError extends FetchError {
  readonly detail: string
  readonly validationIssues?: ValidationIssue[]

  constructor(status: number, detail: string, validationIssues?: ValidationIssue[]) {
    super(status)
    this.name = 'ApiError'
    this.detail = detail
    this.validationIssues = validationIssues
    if (validationIssues && validationIssues.length > 0) {
      this.message = formatValidationIssues(validationIssues)
    } else {
      this.message = `${status}: ${detail}`
    }
  }
}

/**
 * Build a human-readable error string from a FastAPI validation response.
 * Strips the leading `body.` that FastAPI prefixes every location with —
 * showing "email: ..." instead of "body.email: ..." reads better in forms.
 */
export function formatValidationIssues(issues: ValidationIssue[]): string {
  const parts = issues.map((issue) => {
    const field = issue.loc.filter((part) => part !== 'body').join('.')
    return field ? `${field}: ${issue.msg}` : issue.msg
  })
  return parts.join('; ')
}

export interface ApiFetchOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
}

const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

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
    let validationIssues: ValidationIssue[] | undefined
    try {
      const body = (await res.json()) as { detail?: string | ValidationIssue[] }
      if (Array.isArray(body.detail)) {
        // FastAPI validation: detail is a list of Pydantic issue records.
        validationIssues = body.detail
        detail = JSON.stringify(body.detail)
      } else if (typeof body.detail === 'string') {
        detail = body.detail
      }
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, detail, validationIssues)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** Authenticated fetch against portal-api. Cookies + CSRF authenticate. */
export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  try {
    return await doFetch<T>(path, options)
  } catch (err) {
    if (err instanceof UnauthorizedError) {
      authLogger.info('apiFetch received 401 — session expired or missing', { path })
    }
    throw err
  }
}
