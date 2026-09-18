/**
 * TranscriptImportDialog contract (support-gap-detection.md):
 * invalid JSON is rejected before any request, and an analysis that came back
 * `failed` is never reported as a successful import.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

const toastSuccess = vi.fn()
vi.mock('sonner', () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn() } }))

import { TranscriptImportDialog } from '../TranscriptImportDialog'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const orgKbs = [{ id: 1, name: 'Company KB', slug: 'company-kb' }]

async function openDialog() {
  fireEvent.click(screen.getByRole('button', { name: /transcript importeren|import transcript/i }))
  await screen.findByLabelText(/kennisbank|knowledge base/i)
}

function setFile(contents: string) {
  const input = screen.getByLabelText(/transcriptbestand|transcript file/i)
  const file = new File([contents], 'call.json', { type: 'application/json' })
  fireEvent.change(input, { target: { files: [file] } })
  return file
}

beforeEach(() => {
  apiFetchMock.mockReset()
  toastSuccess.mockReset()
  navigate.mockReset()
})

describe('TranscriptImportDialog', () => {
  it('rejects invalid JSON before making any request', async () => {
    render(
      <Wrapper>
        <TranscriptImportDialog orgKbs={orgKbs} />
      </Wrapper>,
    )
    await openDialog()
    setFile('this is not json')

    await waitFor(() => expect(screen.getByText(/geldige json|valid json/i)).toBeTruthy())
    expect(apiFetchMock).not.toHaveBeenCalled()
  })

  it.each(['failed', 'pending'])('does not report an unfinished %s analysis as successful', async (status) => {
    apiFetchMock.mockResolvedValue({ case_id: 1, status, changed: true, findings_count: 0 })
    render(
      <Wrapper>
        <TranscriptImportDialog orgKbs={orgKbs} />
      </Wrapper>,
    )
    await openDialog()
    setFile('{"segments":[],"_source":{"sha256":"abc"}}')
    fireEvent.change(screen.getByLabelText(/kennisbank|knowledge base/i), {
      target: { value: 'company-kb' },
    })

    const submit = () => screen.getByRole('button', { name: /^(importeren|import)$/i })
    await waitFor(() => expect(submit().hasAttribute('disabled')).toBe(false))
    fireEvent.click(submit())

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText(/kon dit transcript niet|could not import/i)).toBeTruthy())
    expect(toastSuccess).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('opens the imported case on success and reloads its cached detail and list, even with no findings', async () => {
    apiFetchMock.mockResolvedValue({ case_id: 42, status: 'analyzed', changed: true, findings_count: 0 })
    // A client that keeps reads fresh for 30s, so a re-import that did not
    // invalidate would keep showing the pre-import detail/list from cache.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
    })
    const detailFetch = vi.fn().mockResolvedValue({ subject: 'Old question' })
    const listFetch = vi.fn().mockResolvedValue({ total: 0 })
    // Prime the detail and a list page as inactive (no observer) but fresh.
    await client.fetchQuery({ queryKey: ['support-case', '42'], queryFn: detailFetch })
    await client.fetchQuery({ queryKey: ['support-cases', 'company-kb', 0], queryFn: listFetch })
    detailFetch.mockResolvedValue({ subject: 'Updated question' })
    listFetch.mockResolvedValue({ total: 1 })

    render(
      <QueryClientProvider client={client}>
        <TranscriptImportDialog orgKbs={orgKbs} />
      </QueryClientProvider>,
    )
    await openDialog()
    setFile('{"segments":[],"_source":{"sha256":"abc"}}')
    fireEvent.change(screen.getByLabelText(/kennisbank|knowledge base/i), {
      target: { value: 'company-kb' },
    })

    const submit = () => screen.getByRole('button', { name: /^(importeren|import)$/i })
    await waitFor(() => expect(submit().hasAttribute('disabled')).toBe(false))
    fireEvent.click(submit())

    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/knowledge/gaps/support-cases/$caseId',
      params: { caseId: '42' },
    })
    expect(await client.fetchQuery({ queryKey: ['support-case', '42'], queryFn: detailFetch }))
      .toEqual({ subject: 'Updated question' })
    expect(await client.fetchQuery({ queryKey: ['support-cases', 'company-kb', 0], queryFn: listFetch }))
      .toEqual({ total: 1 })
  })
})
