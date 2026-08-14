/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 - connector wizard shared utilities.
 *
 * Covers ``joinSeedUrl`` (slash normalisation), now hosted in
 * ../-connector-constants.ts (was ../$kbSlug/-kb-helpers.tsx until the
 * SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 followups). Cookie parsing was
 * intentionally removed earlier: the wizard now collects cookies as
 * structured {name, value} rows via CookieRowsInput, eliminating the
 * parser-layer bug class entirely.
 */

import { describe, expect, it } from 'vitest'
import { isWithinBaseUrl, joinSeedUrl, previewUrlOnDetailsAdvance } from '../-connector-constants'

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

  it('strips both - the redcactus case (the actual reported bug)', () => {
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

/**
 * Preview-URL retention on the details -> next step transition.
 *
 * Reported 2026-08-13: an operator editing a web-crawler connector never saw
 * the interior page they had validated earlier. The wizard DOES prefill the
 * field from the stored `discovery_seed_url` on mount, but the "next" button
 * on the details step overwrote it with the base URL unconditionally, so by
 * the time the preview step rendered the seed was gone from view.
 */
describe('previewUrlOnDetailsAdvance', () => {
  const BASE = 'https://support.ascendcloud.com'
  const SEED = 'https://support.ascendcloud.com/app/articles/detail/a_id/15937'

  it('keeps the stored interior seed instead of resetting to the base URL', () => {
    expect(previewUrlOnDetailsAdvance(SEED, BASE)).toBe(SEED)
  })

  it('keeps the seed when the base URL carries a trailing slash', () => {
    expect(previewUrlOnDetailsAdvance(SEED, `${BASE}/`)).toBe(SEED)
  })

  it('falls back to the base URL when the field is empty', () => {
    expect(previewUrlOnDetailsAdvance('', BASE)).toBe(BASE)
  })

  it('drops a seed that the operator moved out of scope by editing base_url', () => {
    expect(previewUrlOnDetailsAdvance(SEED, 'https://docs.example.com')).toBe('https://docs.example.com')
  })

  it('does not treat a look-alike host as in-scope (prefix-boundary safety)', () => {
    expect(previewUrlOnDetailsAdvance('https://support.ascendcloud.com.evil.test/x', BASE)).toBe(BASE)
  })
})

describe('isWithinBaseUrl', () => {
  it('accepts the base URL itself and any path below it', () => {
    expect(isWithinBaseUrl('https://x.com', 'https://x.com')).toBe(true)
    expect(isWithinBaseUrl('https://x.com/docs/a', 'https://x.com')).toBe(true)
  })

  it('rejects a different site and a look-alike host', () => {
    expect(isWithinBaseUrl('https://y.com/docs', 'https://x.com')).toBe(false)
    expect(isWithinBaseUrl('https://x.com.evil.test/docs', 'https://x.com')).toBe(false)
  })
})
