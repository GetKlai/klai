import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/paraglide/messages', () => ({
  kb_connector_failed_items: ({ count }: { count: string }) => `${count} pagina's niet verwerkt`,
}))

import { FailedItemsWarning } from '../-sources-helpers'
import type { Source } from '../-sources-types'

function connectorSource(overrides: Partial<Source> = {}): Source {
  return {
    kind: 'connector',
    id: 'conn-1',
    name: 'Website (pagina\'s)',
    type_label: 'Website (pagina\'s)',
    connector_type: 'web_crawler',
    items_count: 42,
    chunks_count: 120,
    status: 'ok',
    last_sync_at: '2026-08-14T09:00:00Z',
    created_at: null,
    ...overrides,
  }
}

function uploadSource(overrides: Partial<Source> = {}): Source {
  return {
    kind: 'upload',
    id: 'art-1',
    name: 'report.pdf',
    type_label: 'PDF',
    connector_type: null,
    items_count: 1,
    chunks_count: 3,
    status: null,
    last_sync_at: null,
    created_at: null,
    index_status: 'synced',
    ...overrides,
  }
}

describe('FailedItemsWarning', () => {
  it('shows the failed-pages count for a connector with items_failed_count > 0', () => {
    render(<FailedItemsWarning source={connectorSource({ items_failed_count: 8 })} />)
    expect(screen.getByText("8 pagina's niet verwerkt")).toBeTruthy()
  })

  it('renders nothing when items_failed_count is 0', () => {
    const { container } = render(<FailedItemsWarning source={connectorSource({ items_failed_count: 0 })} />)
    expect(container.textContent).toBe('')
  })

  it('renders nothing when items_failed_count is undefined (older knowledge-ingest versions)', () => {
    const { container } = render(<FailedItemsWarning source={connectorSource({ items_failed_count: undefined })} />)
    expect(container.textContent).toBe('')
  })

  it('renders nothing for uploads, even if items_failed_count were set', () => {
    const { container } = render(<FailedItemsWarning source={uploadSource({ items_failed_count: 5 })} />)
    expect(container.textContent).toBe('')
  })
})
