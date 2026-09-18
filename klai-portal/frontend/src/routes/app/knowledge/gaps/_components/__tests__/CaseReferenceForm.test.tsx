/**
 * Whole-case reference answer key (support-gap-detection.md, gap quality
 * review): the reviewer records every reusable question the case should teach,
 * including ones Klai missed, choosing a diagnosis and citing real conversation
 * segments through the UI (never raw JSON). It is never pre-filled from the
 * machine findings; a completed review with no questions records "nothing
 * reusable". A blank question blocks the save; a 409 is a refresh prompt.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

const toastSuccess = vi.fn()
const toastError = vi.fn()
vi.mock('sonner', () => ({
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) },
}))

import { CaseReferenceForm } from '../CaseReferenceForm'
import { ApiError } from '@/lib/apiFetch'
import type { CaseMessage } from '../../-support-helpers'

function message(overrides: Partial<CaseMessage> = {}): CaseMessage {
  return {
    id: 'msg-1',
    kind: 'message',
    role: 'customer',
    text: 'How do I export my invoices?',
    occurred_at: null,
    visibility: 'customer',
    start_seconds: null,
    end_seconds: null,
    medium: 'email',
    thread_id: null,
    reply_to_id: null,
    speaker_id: null,
    ...overrides,
  }
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const baseProps = {
  caseId: '7',
  kbSlug: 'company-kb',
  contentHash: 'hash-1',
  messages: [message()],
  reference: null,
}

function patchBody() {
  const put = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'PUT')!
  return { path: String(put[0]), body: JSON.parse((put[1] as { body: string }).body) }
}

beforeEach(() => {
  apiFetchMock.mockReset()
  toastSuccess.mockReset()
  toastError.mockReset()
})

describe('CaseReferenceForm', () => {
  it('starts empty (never pre-filled from the analysis) and guides the reviewer', () => {
    render(
      <Wrapper>
        <CaseReferenceForm {...baseProps} />
      </Wrapper>,
    )
    expect(screen.getByText(/no reference questions yet|nog geen referentievragen/i)).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('saves an added question with its diagnosis and cited evidence', async () => {
    apiFetchMock.mockResolvedValue({})
    render(
      <Wrapper>
        <CaseReferenceForm {...baseProps} />
      </Wrapper>,
    )
    fireEvent.click(screen.getByRole('button', { name: /add a question|vraag toevoegen/i }))

    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'How can a customer export invoices themselves?' },
    })
    // Cite the real conversation segment via a UI control, not raw JSON.
    fireEvent.click(screen.getByLabelText(/How do I export my invoices\?/i))

    fireEvent.click(screen.getByRole('button', { name: /save reference|referentie opslaan/i }))

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
    const { path, body } = patchBody()
    expect(path).toBe('/api/app/knowledge-bases/company-kb/support-cases/7/reference')
    expect(body.content_hash).toBe('hash-1')
    expect(body.complete).toBe(false)
    expect(body.questions).toEqual([
      {
        question: 'How can a customer export invoices themselves?',
        diagnosis: 'missing',
        message_ids: ['msg-1'],
      },
    ])
  })

  it('records a complete review with no reusable questions', async () => {
    apiFetchMock.mockResolvedValue({})
    render(
      <Wrapper>
        <CaseReferenceForm {...baseProps} />
      </Wrapper>,
    )
    fireEvent.click(screen.getByLabelText(/reviewed the whole case|hele case beoordeeld/i))
    fireEvent.click(screen.getByRole('button', { name: /save reference|referentie opslaan/i }))

    await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
    const { body } = patchBody()
    expect(body.complete).toBe(true)
    expect(body.questions).toEqual([])
  })

  it('blocks the save while a question is blank', () => {
    render(
      <Wrapper>
        <CaseReferenceForm {...baseProps} />
      </Wrapper>,
    )
    fireEvent.click(screen.getByRole('button', { name: /add a question|vraag toevoegen/i }))
    fireEvent.click(screen.getByRole('button', { name: /save reference|referentie opslaan/i }))

    expect(apiFetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/every question needs text|elke vraag heeft tekst/i)).toBeTruthy()
  })

  it('shows a refresh prompt on a stale 409', async () => {
    apiFetchMock.mockRejectedValue(new ApiError(409, 'stale'))
    render(
      <Wrapper>
        <CaseReferenceForm {...baseProps} reference={{
          content_hash: 'hash-1',
          questions: [{ question: 'Existing reusable question?', diagnosis: 'missing', message_ids: [] }],
          complete: false,
          reviewed_by: 'Reviewer',
          reviewed_at: '2026-09-11T00:00:00Z',
        }} />
      </Wrapper>,
    )
    // Seeded from the saved reference (not the analysis).
    const row = screen.getByDisplayValue('Existing reusable question?')
    expect(row).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /save reference|referentie opslaan/i }))
    await waitFor(() => expect(screen.getByText(/changed since|gewijzigd sinds/i)).toBeTruthy())
    expect(toastSuccess).not.toHaveBeenCalled()
  })
})
