import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/paraglide/messages', () => ({
  kb_status_probleem: () => 'Probleem',
  kb_status_stuck_tooltip: () => 'Hangt vast',
  kb_status_stuck: ({ minutes }: { minutes: string }) => `Hangt al ${minutes}m`,
  kb_status_bezig: () => 'Bezig',
  kb_status_bezig_elapsed: ({ minutes }: { minutes: string }) => `${minutes}m`,
  kb_status_klaar: () => 'Klaar',
  kb_status_leeg: () => 'Leeg',
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
})
