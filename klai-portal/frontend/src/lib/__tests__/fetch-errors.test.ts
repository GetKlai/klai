import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  FetchError,
  RETRYABLE_STATUS,
  UnauthorizedError,
  delay,
  friendlyErrorKey,
  isAborted,
  isRetryable,
} from '../fetch-errors'

describe('UnauthorizedError', () => {
  it('is a distinct named Error subclass', () => {
    const err = new UnauthorizedError()
    expect(err).toBeInstanceOf(Error)
    expect(err).toBeInstanceOf(UnauthorizedError)
    expect(err.name).toBe('UnauthorizedError')
    expect(err.message).toBe('Unauthorized')
  })
})

describe('FetchError', () => {
  it('carries the HTTP status and a conventional message', () => {
    const err = new FetchError(503)
    expect(err).toBeInstanceOf(FetchError)
    expect(err.status).toBe(503)
    expect(err.name).toBe('FetchError')
    expect(err.message).toBe('HTTP 503')
  })

  it('preserves the optional cause chain', () => {
    const root = new TypeError('fetch failed')
    const err = new FetchError(500, { cause: root })
    expect(err.cause).toBe(root)
  })
})

describe('isRetryable', () => {
  it.each([408, 429, 500, 502, 503, 504])('returns true for FetchError status %i', (status) => {
    expect(isRetryable(new FetchError(status))).toBe(true)
  })

  it.each([400, 401, 403, 404, 409, 422])('returns false for FetchError status %i', (status) => {
    expect(isRetryable(new FetchError(status))).toBe(false)
  })

  it('returns true for bare TypeError (network failure from fetch)', () => {
    expect(isRetryable(new TypeError('Failed to fetch'))).toBe(true)
  })

  it('returns false for UnauthorizedError', () => {
    expect(isRetryable(new UnauthorizedError())).toBe(false)
  })

  it('returns false for generic Errors and non-error values', () => {
    expect(isRetryable(new Error('boom'))).toBe(false)
    expect(isRetryable('boom')).toBe(false)
    expect(isRetryable(null)).toBe(false)
    expect(isRetryable(undefined)).toBe(false)
  })
})

describe('isAborted', () => {
  it('returns true for DOMException("AbortError")', () => {
    expect(isAborted(new DOMException('Aborted', 'AbortError'))).toBe(true)
  })

  it('returns false for other DOMException names', () => {
    expect(isAborted(new DOMException('Nope', 'NotFoundError'))).toBe(false)
  })

  it('returns false for plain errors and primitives', () => {
    expect(isAborted(new Error('AbortError'))).toBe(false)
    expect(isAborted(new TypeError('Failed to fetch'))).toBe(false)
    expect(isAborted(null)).toBe(false)
  })
})

describe('friendlyErrorKey', () => {
  it('maps TypeError to "network"', () => {
    expect(friendlyErrorKey(new TypeError('Failed to fetch'))).toBe('network')
  })

  it('maps 404 to "not_found"', () => {
    expect(friendlyErrorKey(new FetchError(404))).toBe('not_found')
  })

  it('maps 403 to "forbidden"', () => {
    expect(friendlyErrorKey(new FetchError(403))).toBe('forbidden')
  })

  it.each([408, 429, 500, 503])('maps retryable status %i to "server_temporary"', (status) => {
    expect(friendlyErrorKey(new FetchError(status))).toBe('server_temporary')
  })

  it('falls back to "generic" for unknown errors', () => {
    expect(friendlyErrorKey(new FetchError(400))).toBe('generic')
    expect(friendlyErrorKey(new UnauthorizedError())).toBe('generic')
    expect(friendlyErrorKey(new Error('boom'))).toBe('generic')
    expect(friendlyErrorKey(null)).toBe('generic')
  })
})

describe('RETRYABLE_STATUS', () => {
  it('covers the standard transient status codes', () => {
    for (const status of [408, 429, 500, 502, 503, 504]) {
      expect(RETRYABLE_STATUS.has(status)).toBe(true)
    }
  })

  it('excludes permanent client errors', () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
      expect(RETRYABLE_STATUS.has(status)).toBe(false)
    }
  })
})

describe('delay', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('resolves after the specified duration', async () => {
    const promise = delay(1000)
    vi.advanceTimersByTime(999)
    let resolved = false
    void promise.then(() => {
      resolved = true
    })
    await Promise.resolve()
    expect(resolved).toBe(false)
    vi.advanceTimersByTime(1)
    await promise
    expect(resolved).toBe(true)
  })

  it('rejects with AbortError when the signal is already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(delay(1000, controller.signal)).rejects.toSatisfy(isAborted)
  })

  it('rejects with AbortError when the signal fires during the wait', async () => {
    const controller = new AbortController()
    const promise = delay(5000, controller.signal)
    controller.abort()
    await expect(promise).rejects.toSatisfy(isAborted)
  })

  it('does not leak timers when aborted mid-wait', async () => {
    const controller = new AbortController()
    const promise = delay(5000, controller.signal)
    controller.abort()
    await expect(promise).rejects.toSatisfy(isAborted)
    // Advancing time further must not produce unhandled rejections.
    vi.advanceTimersByTime(10_000)
  })
})
