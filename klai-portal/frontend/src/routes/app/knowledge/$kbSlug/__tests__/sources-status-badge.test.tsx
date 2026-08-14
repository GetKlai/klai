import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/paraglide/messages', () => ({
  kb_status_probleem: () => 'Probleem',
  kb_status_stuck_tooltip: () => 'Hangt vast',
  kb_status_stuck: ({ minutes }: { minutes: string }) => `Hangt al ${minutes}m`,
  kb_status_bezig: () => 'Bezig',
  kb_status_bezig_elapsed: ({ minutes }: { minutes: string }) => `${minutes}m`,
  kb_status_klaar: () => 'Klaar',
  kb_status_leeg: () => 'Leeg',
  kb_status_failed_tooltip: () => 'Verwerking mislukt — probeer opnieuw',
}))

import { StatusBadge } from '../-sources-helpers'
import type { Source } from '../-sources-types'

function uploadSource(overrides: Partial<Source> = {}): Source {
  return {
    kind: 'upload',
    id: 'art-1',
    name: 'https://www.example.com/',
    type_label: 'Websitepagina',
    connector_type: null,
    items_count: 1,
    chunks_count: 0,
    status: null,
    last_sync_at: null,
    created_at: null,
    index_status: 'synced',
    ...overrides,
  }
}

describe('StatusBadge', () => {
  it('shows a destructive Probleem badge for failed uploads instead of neutral Leeg', () => {
    // Regression: index_status='failed' uploads rendered the neutral "Leeg"
    // badge, so a permanently failed source went unnoticed for 8 days.
    render(<StatusBadge source={uploadSource({ index_status: 'failed' })} />)

    const badge = screen.getByText('Probleem')
    expect(badge).toBeTruthy()
    expect(badge.getAttribute('title')).toBe('Verwerking mislukt — probeer opnieuw')
    expect(screen.queryByText('Leeg')).toBeNull()
  })

  it('still shows Klaar for synced uploads', () => {
    render(<StatusBadge source={uploadSource({ index_status: 'synced', chunks_count: 3 })} />)
    expect(screen.getByText('Klaar')).toBeTruthy()
  })
})
