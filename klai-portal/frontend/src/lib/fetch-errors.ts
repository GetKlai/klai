/**
 * Typed HTTP fetch errors + retry utilities for the portal frontend.
 *
 * Centralises transient-vs-permanent failure classification for any code path
 * that talks to portal-api from the browser. Producers throw one of:
 *   - UnauthorizedError   — 401 response; caller must force a fresh sign-in
 *   - FetchError          — any other non-OK response, carries the status
 *   - TypeError (native)  — network-layer failure from the fetch() call itself
 *
 * A DOMException with name === "AbortError" is the only outcome we treat as
 * "not an error at all" — it signals a cancelled request, never user-facing.
 */

/** HTTP status codes that warrant a retry — transient server/network issues. */
export const RETRYABLE_STATUS: ReadonlySet<number> = new Set([
  408, // Request Timeout
  429, // Too Many Requests
  500, // Internal Server Error
  502, // Bad Gateway
  503, // Service Unavailable
  504, // Gateway Timeout
])

/** The access_token has been rejected — the caller must force a fresh sign-in. */
export class UnauthorizedError extends Error {
  constructor() {
    super('Unauthorized')
    this.name = 'UnauthorizedError'
  }
}

/** Any other non-OK HTTP response from portal-api. */
export class FetchError extends Error {
  readonly status: number

  constructor(status: number, options?: ErrorOptions) {
    super(`HTTP ${status}`, options)
    this.name = 'FetchError'
    this.status = status
  }
}

/** True for failures that are likely transient and worth retrying. */
export function isRetryable(err: unknown): boolean {
  if (err instanceof UnauthorizedError) return false
  if (err instanceof FetchError) return RETRYABLE_STATUS.has(err.status)
  // Bare fetch() rejections (DNS/CORS/connection/extension) surface as TypeError.
  return err instanceof TypeError
}

/** True when the error represents a cancelled fetch — never surface to users. */
export function isAborted(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/**
 * Coarse-grained error category used to pick a friendly i18n message.
 * Keep the number of buckets small so translations stay maintainable.
 */
export type FriendlyErrorKey =
  | 'network'
  | 'server_temporary'
  | 'not_found'
  | 'forbidden'
  | 'generic'

/** Map any error to its user-facing category. */
export function friendlyErrorKey(err: unknown): FriendlyErrorKey {
  if (err instanceof TypeError) return 'network'
  if (err instanceof FetchError) {
    if (err.status === 404) return 'not_found'
    if (err.status === 403) return 'forbidden'
    if (RETRYABLE_STATUS.has(err.status)) return 'server_temporary'
  }
  return 'generic'
}

/**
 * Abort-aware sleep. Resolves after `ms` unless the signal fires, in which
 * case it rejects with an AbortError — matching fetch()'s own abort semantics.
 */
export function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}
