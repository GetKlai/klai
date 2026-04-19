import { describe, it, expect } from 'vitest'
import { ErrorResponse } from 'oidc-client-ts'
import {
  REAUTHENTICATION_ERRORS,
  extractOidcErrorCode,
  isReauthenticationRequired,
} from '../oidc-error'

describe('extractOidcErrorCode', () => {
  it('reads .error from a direct ErrorResponse', () => {
    const err = new ErrorResponse({ error: 'login_required' })
    expect(extractOidcErrorCode(err)).toBe('login_required')
  })

  it('reads .innerError.error from a silent-renew wrapper', () => {
    // Shape observed in production when oidc-client-ts v3.5.x wraps an iframe
    // login_required response from Zitadel.
    const err = {
      name: 'ErrorResponse',
      message: 'login_required',
      source: 'signinSilent',
      innerError: { error: 'login_required', name: 'ErrorResponse' },
    }
    expect(extractOidcErrorCode(err)).toBe('login_required')
  })

  it('prefers the outer .error when both outer and innerError have one', () => {
    const err = {
      error: 'invalid_grant',
      innerError: { error: 'login_required' },
    }
    expect(extractOidcErrorCode(err)).toBe('invalid_grant')
  })

  it('falls back to .message when it matches a known OIDC code', () => {
    const err = new Error('invalid_grant')
    expect(extractOidcErrorCode(err)).toBe('invalid_grant')
  })

  it('ignores .message when it does not match a known OIDC code', () => {
    expect(extractOidcErrorCode(new Error('Network request failed'))).toBeNull()
    expect(extractOidcErrorCode(new Error(''))).toBeNull()
  })

  it('returns null for primitives and nullish values', () => {
    expect(extractOidcErrorCode(null)).toBeNull()
    expect(extractOidcErrorCode(undefined)).toBeNull()
    expect(extractOidcErrorCode('login_required')).toBeNull()
    expect(extractOidcErrorCode(42)).toBeNull()
    expect(extractOidcErrorCode(true)).toBeNull()
  })

  it('returns null when .error is an empty string', () => {
    expect(extractOidcErrorCode({ error: '' })).toBeNull()
  })
})

describe('isReauthenticationRequired', () => {
  it.each([
    'invalid_grant',
    'login_required',
    'interaction_required',
    'consent_required',
    'account_selection_required',
  ])('returns true for %s', (code) => {
    expect(isReauthenticationRequired({ error: code })).toBe(true)
  })

  it('returns true for a wrapped login_required silent-renew error', () => {
    expect(
      isReauthenticationRequired({
        source: 'signinSilent',
        innerError: { error: 'login_required' },
      }),
    ).toBe(true)
  })

  it('returns false for non-reauth OIDC errors', () => {
    expect(isReauthenticationRequired({ error: 'server_error' })).toBe(false)
    expect(isReauthenticationRequired({ error: 'temporarily_unavailable' })).toBe(false)
  })

  it('returns false for null/undefined and generic errors', () => {
    expect(isReauthenticationRequired(null)).toBe(false)
    expect(isReauthenticationRequired(undefined)).toBe(false)
    expect(isReauthenticationRequired(new Error('boom'))).toBe(false)
  })
})

describe('REAUTHENTICATION_ERRORS', () => {
  it('covers the core silent-renew failure codes', () => {
    expect(REAUTHENTICATION_ERRORS.has('login_required')).toBe(true)
    expect(REAUTHENTICATION_ERRORS.has('invalid_grant')).toBe(true)
    expect(REAUTHENTICATION_ERRORS.has('interaction_required')).toBe(true)
    expect(REAUTHENTICATION_ERRORS.has('consent_required')).toBe(true)
  })
})
