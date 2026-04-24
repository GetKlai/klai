/**
 * Direct tests for the error-mapping logic in useSourceSubmit.ts.
 *
 * These cover the SPEC D8 HTTP-status → i18n-key translation table — the
 * hook itself (mutation wiring, navigation) is exercised via the form
 * components in end-to-end smoke rather than here.
 */
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/apiFetch'
import { errorMessageFor, extractErrorCode } from '../useSourceSubmit'

describe('extractErrorCode', () => {
  it('returns the error_code from a stringified dict detail', () => {
    const detail = JSON.stringify({ error_code: 'kb_quota_items_exceeded', limit: 20 })
    expect(extractErrorCode(detail)).toBe('kb_quota_items_exceeded')
  })

  it('returns undefined for plain string detail', () => {
    expect(extractErrorCode('Not a valid URL')).toBeUndefined()
  })

  it('returns undefined for empty detail', () => {
    expect(extractErrorCode('')).toBeUndefined()
  })

  it('returns undefined when the parsed object lacks error_code', () => {
    expect(extractErrorCode(JSON.stringify({ message: 'nope' }))).toBeUndefined()
  })

  it('returns undefined when error_code is not a string', () => {
    expect(extractErrorCode(JSON.stringify({ error_code: 42 }))).toBeUndefined()
  })
})

describe('errorMessageFor — SPEC D8 mapping', () => {
  // Mirror of the mapping we expect. Keeps the test readable AND guards
  // against silent i18n-key renames (compiler catches missing keys at
  // build time, and the runtime assertion catches wiring regressions).
  function keyOf(message: string): string {
    // Paraglide messages in dev don't have a stable "key" handle — assert
    // on the message text we wrote in messages/en.json. If the EN copy is
    // ever retranslated, this is where to update the test.
    return message
  }

  it('non-ApiError → generic banner', () => {
    expect(errorMessageFor('url', new Error('boom'))).toBe(
      keyOf('Something went wrong. Try again.'),
    )
  })

  it('ApiError 403 with kb_quota_items_exceeded error_code → kb full banner', () => {
    const err = new ApiError(
      403,
      JSON.stringify({ error_code: 'kb_quota_items_exceeded', plan: 'core', limit: 20, current: 20 }),
    )
    expect(errorMessageFor('text', err)).toBe(
      keyOf('This knowledge base has reached its document limit'),
    )
  })

  it('ApiError 400 with "not allowed" in detail → blocked URL banner', () => {
    const err = new ApiError(400, 'This URL is not allowed')
    expect(errorMessageFor('url', err)).toBe(keyOf('This URL is not allowed'))
  })

  it('ApiError 400 with generic message → invalid URL banner (URL kind)', () => {
    const err = new ApiError(400, 'Not a valid URL')
    expect(errorMessageFor('url', err)).toBe(keyOf('Not a valid URL'))
  })

  it('ApiError 400 with generic message → invalid URL banner (YouTube kind)', () => {
    const err = new ApiError(400, 'Not a valid YouTube URL')
    expect(errorMessageFor('youtube', err)).toBe(keyOf('Not a valid URL'))
  })

  it('ApiError 422 on YouTube → no-transcript banner', () => {
    const err = new ApiError(422, 'This video has no transcript available')
    expect(errorMessageFor('youtube', err)).toBe(
      keyOf('This video has no transcript available'),
    )
  })

  it('ApiError 422 on URL kind → generic (422 is unexpected outside YouTube)', () => {
    const err = new ApiError(422, 'Validation failed')
    expect(errorMessageFor('url', err)).toBe(keyOf('Something went wrong. Try again.'))
  })

  it('ApiError 502 on URL kind → fetch-failed banner', () => {
    const err = new ApiError(502, 'crawl4ai unreachable')
    expect(errorMessageFor('url', err)).toBe(
      keyOf('Could not reach the page — try again'),
    )
  })

  it('ApiError 502 on YouTube kind → YouTube-specific unreachable banner', () => {
    // IP block / rate-limit on core-01 surfaces here — user needs to know
    // it's a transient upstream issue, not "no transcript".
    const err = new ApiError(502, 'Could not reach YouTube — try again')
    expect(errorMessageFor('youtube', err)).toBe(
      keyOf('Could not reach YouTube — try again'),
    )
  })

  it('ApiError 502 on Text kind → generic fetch-failed (no upstream for text)', () => {
    // Text never calls crawl4ai or YouTube — a 502 here is from the
    // knowledge-ingest forwarding step. Generic fetch-failed copy is fine.
    const err = new ApiError(502, 'Knowledge ingest unreachable')
    expect(errorMessageFor('text', err)).toBe(
      keyOf('Could not reach the page — try again'),
    )
  })

  it('ApiError 500 or any unmapped status → generic banner', () => {
    const err = new ApiError(500, 'Internal Server Error')
    expect(errorMessageFor('text', err)).toBe(
      keyOf('Something went wrong. Try again.'),
    )
  })

  it('error_code wins over status-based fallback (403 dict > 403 string)', () => {
    // Even though we've only mapped 403 via error_code, verify the code
    // path prioritises error_code inspection before falling through to
    // the switch on status. 403 without a known code → generic.
    const withCode = new ApiError(403, JSON.stringify({ error_code: 'kb_quota_items_exceeded' }))
    const withoutCode = new ApiError(403, 'Some other 403')
    expect(errorMessageFor('text', withCode)).toBe(
      keyOf('This knowledge base has reached its document limit'),
    )
    expect(errorMessageFor('text', withoutCode)).toBe(
      keyOf('Something went wrong. Try again.'),
    )
  })
})
