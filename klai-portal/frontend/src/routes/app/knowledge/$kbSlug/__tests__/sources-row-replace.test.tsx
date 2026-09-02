/**
 * Row-level wiring for the "replace file" row action.
 *
 * Three things only this layer can prove:
 *
 *   1. The file picker exists ONLY for sources backed by a file upload
 *      (`can_replace`) — the same flag gates the menu item. Offering it on
 *      a URL or pasted-text source would 404 on click, because there is no
 *      file behind them to replace.
 *   2. Picking a file posts it to the replace endpoint — the hidden input
 *      and the menu item are actually connected.
 *   3. A rejected file leaves a readable reason on the row instead of
 *      failing silently, so the user knows the source was NOT replaced.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})
vi.mock('@/lib/logger', () => ({
  queryLogger: { error: vi.fn() },
}))

import * as m from '@/paraglide/messages'
import { ApiError } from '@/lib/apiFetch'
import { SourceRow } from '../-sources-row'
import type { Source } from '../-sources-types'

function uploadSource(overrides: Partial<Source> = {}): Source {
  return {
    kind: 'upload',
    id: 'art-1',
    name: 'llm_leeswijzer.md',
    type_label: 'Plain Text',
    connector_type: null,
    items_count: 1,
    chunks_count: 4,
    status: null,
    last_sync_at: null,
    created_at: null,
    index_status: 'synced',
    can_replace: true,
    ...overrides,
  }
}

function renderRow(source: Source) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return render(
    <SourceRow
      source={source}
      expanded={false}
      onToggle={() => undefined}
      kbSlug="sip"
      editablePageId={null}
    />,
    { wrapper: Wrapper },
  )
}

beforeEach(() => {
  apiFetchMock.mockReset()
})

describe('SourceRow replace action', () => {
  it('offers a file picker for a file-backed source', () => {
    renderRow(uploadSource())
    expect(screen.getByLabelText(m.kb_sources_row_replace_file())).toBeTruthy()
  })

  it('offers none for a source that did not come from a file', () => {
    renderRow(uploadSource({ can_replace: false, name: 'example.com', type_label: 'Website' }))
    expect(screen.queryByLabelText(m.kb_sources_row_replace_file())).toBeNull()
  })

  it('posts the picked file to the replace endpoint', async () => {
    apiFetchMock.mockResolvedValue({ id: 'upl-1', filename: 'llm_leeswijzer.md', status: 'done' })
    renderRow(uploadSource())

    const input = screen.getByLabelText(m.kb_sources_row_replace_file())
    fireEvent.change(input, {
      target: { files: [new File(['# v2'], 'llm_leeswijzer.md', { type: 'text/markdown' })] },
    })

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1))
    const [path, options] = apiFetchMock.mock.calls[0] as [string, { method: string, body: FormData }]
    expect(path).toBe('/api/app/knowledge-bases/sip/uploads/art-1/replace')
    expect(options.method).toBe('POST')
    expect((options.body.get('file') as File).name).toBe('llm_leeswijzer.md')
  })

  it('shows why a rejected file did not replace the source', async () => {
    apiFetchMock.mockRejectedValue(
      new ApiError(400, JSON.stringify({ error_code: 'file_too_large' })),
    )
    renderRow(uploadSource())

    const input = screen.getByLabelText(m.kb_sources_row_replace_file())
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'groot.pdf', { type: 'application/pdf' })] },
    })

    expect(await screen.findByText(/Bestand te groot/)).toBeTruthy()
  })
})
