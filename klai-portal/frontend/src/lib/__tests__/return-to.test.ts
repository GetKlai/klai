import { describe, expect, it } from 'vitest'
import { safeReturnTo } from '../auth-context'

describe('safeReturnTo', () => {
  it.each([
    undefined,
    '',
    'https://evil.example.net',
    '//evil.example.net',
    '/app?next=https://evil.example.net',
  ])('refuses a target that is not a same-origin path: %s', (value) => {
    expect(safeReturnTo(value)).toBe('/app')
  })

  it.each([
    '/\t/evil.example.net',
    '/app\n',
    '/app\r',
    '/app\u0000',
    '/app\u007f',
    '/app ',
    '/\\evil.example.net',
    '/app\\settings',
  ])('refuses a return target with control characters, a trailing space or a backslash in its path: %j', (value) => {
    expect(safeReturnTo(value)).toBe('/app')
  })

  it.each([
    '/app/dashboard',
    '/app/dashboard?foo=bar%20baz',
    '/oauth/authorize?redirect_uri=http%3A%2F%2Flocalhost%3A54321%2Fcallback&state=a\\b',
  ])('keeps a same-origin path unchanged: %s', (value) => {
    expect(safeReturnTo(value)).toBe(value)
  })
})
