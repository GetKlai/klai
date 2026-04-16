import { describe, it, expect } from 'vitest'
import { resolveSlug, shortId, PageNotInIndexError } from '../KBEditorContext'
import type { PageIndexEntry } from '../KBEditorContext'

const INDEX: PageIndexEntry[] = [
  { id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890', slug: 'getting-started', title: 'Getting Started' },
  { id: 'b2c3d4e5-f6a7-8901-bcde-f12345678901', slug: 'advanced/config', title: 'Advanced Config' },
  { id: null, slug: 'draft-page', title: 'Draft (no UUID yet)' },
]

describe('resolveSlug (strict mode)', () => {
  it('resolves a full UUID to its slug', () => {
    expect(resolveSlug('a1b2c3d4-e5f6-7890-abcd-ef1234567890', INDEX))
      .toBe('getting-started')
  })

  it('resolves a slug to itself when it exists in the index', () => {
    expect(resolveSlug('getting-started', INDEX)).toBe('getting-started')
  })

  it('resolves a nested slug path correctly', () => {
    expect(resolveSlug('advanced/config', INDEX)).toBe('advanced/config')
  })

  it('throws PageNotInIndexError when UUID is not found — REQ-STA-04', () => {
    expect(() => resolveSlug('00000000-0000-0000-0000-000000000000', INDEX))
      .toThrow(PageNotInIndexError)
  })

  it('throws PageNotInIndexError when slug is not found — REQ-STA-04', () => {
    expect(() => resolveSlug('nonexistent-page', INDEX))
      .toThrow(PageNotInIndexError)
  })

  it('does NOT fall back to treating a UUID as a slug — REQ-UNW-02', () => {
    // A valid UUID that is not in the index must throw, not be passed to the API as-is
    const validUuidNotInIndex = 'deadbeef-dead-beef-dead-beefdeadbeef'
    expect(() => resolveSlug(validUuidNotInIndex, INDEX))
      .toThrow(PageNotInIndexError)
  })

  it('returns the slug for a page with null id when looked up by slug', () => {
    expect(resolveSlug('draft-page', INDEX)).toBe('draft-page')
  })

  it('throws when pageIndex is empty', () => {
    expect(() => resolveSlug('getting-started', [])).toThrow(PageNotInIndexError)
  })
})

describe('shortId', () => {
  it('returns the UUID id when present', () => {
    const entry: PageIndexEntry = {
      id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
      slug: 'getting-started',
      title: 'Getting Started',
    }
    expect(shortId(entry)).toBe('a1b2c3d4-e5f6-7890-abcd-ef1234567890')
  })

  it('returns empty string when entry has no id — no slug fallback', () => {
    const entry: PageIndexEntry = { id: null, slug: 'draft-page', title: 'Draft' }
    expect(shortId(entry)).toBe('')
  })

  it('returns empty string when entry is undefined', () => {
    expect(shortId(undefined)).toBe('')
  })
})
