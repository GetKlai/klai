import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/paraglide/messages', () => ({
  kb_status_probleem: () => 'Probleem',
  kb_status_stuck_tooltip: () => 'Hangt vast',
  kb_status_stuck: ({ minutes }: { minutes: string }) => `Hangt al ${minutes}m`,
  kb_status_bezig: () => 'Bezig',
  kb_status_bezig_elapsed: ({ minutes }: { minutes: string }) => `${minutes}m`,
  kb_status_klaar: () => 'Klaar',
  kb_status_leeg: () => 'Leeg',
  kb_status_failed_tooltip: () => 'Verwerking mislukt',
}))

import { mapSourceStatus, shouldPollSource } from '../-sources-helpers'
import type { Source } from '../-sources-types'

function uploadSource(overrides: Partial<Source> = {}): Source {
  return {
    kind: 'upload',
    id: 'art-1',
    name: 'team-check-in-klee-samenvatting.md',
    type_label: 'Kb Article',
    connector_type: null,
    items_count: 1,
    chunks_count: 0,
    status: null,
    last_sync_at: null,
    created_at: null,
    index_status: 'pending',
    ...overrides,
  }
}

describe('sources helpers', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps stale pending sources visible as pending without endless polling', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-06-05T15:10:00Z'))

    const source = uploadSource({ created_at: '2026-06-05T14:55:00Z' })

    expect(mapSourceStatus(source)).toBe('pending')
    expect(shouldPollSource(source)).toBe(false)
  })

  it('polls fresh pending sources', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-06-05T15:10:00Z'))

    expect(shouldPollSource(uploadSource({ created_at: '2026-06-05T15:05:30Z' }))).toBe(true)
  })

  it('does not poll terminal sources', () => {
    expect(shouldPollSource(uploadSource({ index_status: 'synced', chunks_count: 4 }))).toBe(false)
  })

  it('resumes polling when an old source is re-synced (last_sync_at fresh, created_at stale)', () => {
    // Regression: a re-synced 8-day-old artifact measured elapsed time from
    // created_at, so it showed "Hangt al 11384m" immediately and polling
    // never resumed. The backend now sets last_sync_at to the reindex start.
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-14T09:20:00Z'))

    const resynced = uploadSource({
      created_at: '2026-08-06T11:30:00Z',
      last_sync_at: '2026-08-14T09:13:00Z',
    })

    expect(mapSourceStatus(resynced)).toBe('pending')
    expect(shouldPollSource(resynced)).toBe(true)
  })

  it('maps failed uploads to not_synced', () => {
    expect(mapSourceStatus(uploadSource({ index_status: 'failed' }))).toBe('not_synced')
  })
})
