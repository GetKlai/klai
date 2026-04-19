/**
 * OIDC error classification helpers.
 *
 * oidc-client-ts v3 surfaces silent-renew failures via
 * `events.addSilentRenewError(error)` and `auth.error`. The error shape
 * varies across library versions and error paths:
 *
 *   - a plain ErrorResponse with `.error` (OIDC error code, e.g. "login_required")
 *   - a wrapper with `.innerError: ErrorResponse` plus metadata (`source`, `message`)
 *   - a generic Error where the OIDC code has leaked into `.message`
 *
 * `instanceof ErrorResponse` is unreliable across module duplication, HMR, and
 * future wrapper classes. Inspect well-known fields instead.
 *
 * Codes taken from the OIDC Core spec:
 *   https://openid.net/specs/openid-connect-core-1_0.html#AuthError
 */

/** OIDC/OAuth error codes that indicate the user must re-authenticate. */
export const REAUTHENTICATION_ERRORS: ReadonlySet<string> = new Set([
  'invalid_grant',             // refresh_token expired or revoked
  'login_required',            // silent iframe renew — no active session at the OP
  'interaction_required',      // OP requires user interaction
  'consent_required',          // OP requires explicit consent
  'account_selection_required' // OP needs the user to pick an account
])

/**
 * Extract the OIDC error code from any shape of error thrown by oidc-client-ts
 * or its wrappers. Returns null when no recognizable code is found.
 */
export function extractOidcErrorCode(err: unknown): string | null {
  if (!err || typeof err !== 'object') return null

  // Direct ErrorResponse: { error: "login_required", ... }
  const top = err as Record<string, unknown>
  if (typeof top.error === 'string' && top.error) return top.error

  // Wrapped: { innerError: ErrorResponse, source: "signinSilent", ... }
  const inner = top.innerError
  if (inner && typeof inner === 'object') {
    const innerRecord = inner as Record<string, unknown>
    if (typeof innerRecord.error === 'string' && innerRecord.error) return innerRecord.error
  }

  // Fallback: some wrappers propagate the OIDC code into .message
  if (typeof top.message === 'string' && REAUTHENTICATION_ERRORS.has(top.message)) {
    return top.message
  }

  return null
}

/** True when the error means the user must re-authenticate with the OP. */
export function isReauthenticationRequired(err: unknown): boolean {
  const code = extractOidcErrorCode(err)
  return code !== null && REAUTHENTICATION_ERRORS.has(code)
}
