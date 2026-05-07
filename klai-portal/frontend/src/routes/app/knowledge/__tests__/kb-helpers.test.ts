/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 — kb-helpers shared utilities.
 *
 * Covers ``joinSeedUrl`` (slash normalisation). Cookie parsing was
 * intentionally removed: the wizard now collects cookies as structured
 * {name, value} rows via CookieRowsInput, eliminating the parser-layer
 * bug class entirely.
 */

import { describe, expect, it } from 'vitest'
import { joinSeedUrl } from '../$kbSlug/-kb-helpers'

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
