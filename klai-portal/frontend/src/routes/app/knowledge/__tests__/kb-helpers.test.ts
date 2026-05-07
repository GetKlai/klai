/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 hotfix — kb-helpers shared utilities.
 *
 * Covers ``joinSeedUrl`` (slash normalisation) and the bare-value cookie
 * parsing path added after operators kept pasting just the cookie VALUE
 * column from DevTools Application > Cookies panel.
 */

import { describe, expect, it } from 'vitest'
import { joinSeedUrl, parseCookieString } from '../$kbSlug/-kb-helpers'

describe('joinSeedUrl', () => {
  it('combines clean base + path with single slash', () => {
    expect(joinSeedUrl('https://x.com', 'docs')).toBe('https://x.com/docs/')
  })

  it('strips trailing slash from base', () => {
    expect(joinSeedUrl('https://x.com/', 'docs')).toBe('https://x.com/docs/')
  })

  it('strips leading slash from path', () => {
    expect(joinSeedUrl('https://x.com', '/docs')).toBe('https://x.com/docs/')
  })

  it('strips both — the redcactus case (the actual reported bug)', () => {
    expect(joinSeedUrl('https://wiki.redcactus.cloud/', '/nl/')).toBe(
      'https://wiki.redcactus.cloud/nl/',
    )
  })

  it('handles multi-level path', () => {
    expect(joinSeedUrl('https://x.com', '/nl/articles/')).toBe(
      'https://x.com/nl/articles/',
    )
  })

  it('handles empty path → keeps trailing slash on base', () => {
    expect(joinSeedUrl('https://x.com', '')).toBe('https://x.com/')
  })

  it('handles undefined-equivalent (empty string from useState)', () => {
    expect(joinSeedUrl('https://x.com/', '')).toBe('https://x.com/')
  })
})

describe('parseCookieString', () => {
  const baseUrl = 'https://wiki.redcactus.cloud'

  it('returns undefined for empty string', () => {
    expect(parseCookieString('', baseUrl)).toBeUndefined()
  })

  it('parses JSON array format', () => {
    const out = parseCookieString(
      '[{"name":"sess","value":"abc"}]',
      baseUrl,
    )
    expect(out).toEqual([{ name: 'sess', value: 'abc' }])
  })

  it('parses header format with one cookie', () => {
    const out = parseCookieString('sess=abc', baseUrl) as Array<{
      name: string
      value: string
      domain: string
    }>
    expect(out).toHaveLength(1)
    expect(out[0].name).toBe('sess')
    expect(out[0].value).toBe('abc')
    expect(out[0].domain).toBe('wiki.redcactus.cloud')
  })

  it('parses header format with multiple cookies', () => {
    const out = parseCookieString('a=1; b=2; c=3', baseUrl) as Array<{
      name: string
      value: string
    }>
    expect(out).toHaveLength(3)
    expect(out.map((c) => c.name)).toEqual(['a', 'b', 'c'])
  })

  it('handles bare value (no name=value syntax) — auto-names "session"', () => {
    // Operator pasted just the Value column from DevTools.
    // The actual reported case: redcactus session cookie value, base64 chars.
    const bareValue =
      'eyJpdil6ImlaMzFDbDFyZFZKd2IyOVRUR05aYjNRNVJGRTlQU0lzSW5aaGJIVmxJam9pUWt4c1YwaGtUQ3ROZFZrMk1qVjRSak5VSzJ0VEszSnlSRkZLUW1sNFJuUktPVXN3UzBOUE1tdHdaMFozVEV0alRFa3JVWGh6WTAxRmNIcGlkRWN5U1ZWSlMwTnNNRkpVZERKWWIzZDZWRlYzYzBSUGNuaEpjbFUwUjNOR'
    const out = parseCookieString(bareValue, baseUrl) as Array<{
      name: string
      value: string
    }>
    expect(out).toHaveLength(1)
    expect(out[0].name).toBe('session')
    expect(out[0].value).toBe(bareValue)
  })

  it('does NOT trigger bare-value path when string contains "="', () => {
    // Even if the value LOOKS like it has a name= prefix, treat as header.
    expect(parseCookieString('foo=bar', baseUrl)).toEqual([
      { name: 'foo', value: 'bar', domain: 'wiki.redcactus.cloud', path: '/' },
    ])
  })

  it('does NOT trigger bare-value path when string contains ";"', () => {
    // Multi-cookie format must always go through the header parser.
    const out = parseCookieString('a=1; b=2', baseUrl) as Array<unknown>
    expect(out).toHaveLength(2)
  })
})
