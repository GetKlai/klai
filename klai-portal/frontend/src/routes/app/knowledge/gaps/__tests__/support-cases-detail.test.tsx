/**
 * Support-case detail contract (support-gap-detection.md, shared UI contract):
 * a pending case renders its status instead of an empty analysis, a finding
 * shows the conversation excerpts it cites, and a human review is saved against
 * the exact analysis_revision the reviewer saw — a stale save (409) is shown as
 * a refresh prompt, never as a successful save.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const params = { caseId: '7' }

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    Link: ({ to, children }: { to: string; children?: ReactNode }) => <a href={to}>{children}</a>,
    createFileRoute: () => (cfg: unknown) => ({ ...(cfg as object), useParams: () => params }),
  }
})

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

import { SupportCaseDetailPage } from '../support-cases.$caseId'
import { ApiError } from '@/lib/apiFetch'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function message(overrides: Record<string, unknown> = {}) {
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

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: '7',
    kb_slug: 'company-kb',
    status: 'analyzed',
    analysis_version: 'v1',
    analysis_revision: 'rev-abc',
    content_hash: 'hash-1',
    reference: null,
    imported_at: '2026-09-10T08:00:00Z',
    payload: {
      source: 'hubspot',
      account_id: 'a',
      external_id: 'e',
      subject: 'Invoice export',
      language: 'en',
      source_url: null,
      source_updated_at: null,
      complete: true,
      incomplete_reasons: [],
      messages: [message()],
      metadata: {},
    },
    analysis: [
      {
        question: 'How do I export invoices?',
        language: 'en',
        diagnosis: 'missing',
        rationale: 'No article explains invoice export.',
        missing_information: 'A how-to for invoice export.',
        audience: 'customer',
        message_ids: ['msg-1'],
        articles: [],
        gap_type: 'content',
        top_score: 0.1,
        review: null,
      },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  apiFetchMock.mockReset()
  toastSuccess.mockReset()
  toastError.mockReset()
})

describe('SupportCaseDetailPage', () => {
  it('offers role correction for call evidence received through HubSpot', async () => {
    const data = detail()
    data.payload.messages = [
      message({ kind: 'transcript', medium: 'call', role: 'unknown' }),
      message({ id: 'email-2', kind: 'email', medium: 'email', role: 'customer' }),
    ]
    apiFetchMock.mockResolvedValue(data)
    render(<Wrapper><SupportCaseDetailPage /></Wrapper>)
    const summary = (await screen.findAllByText(/speaker roles|sprekerrollen/i)).find((el) => el.tagName === 'SUMMARY')!
    fireEvent.click(summary)
    expect(screen.getAllByLabelText(/role for this segment|rol voor dit segment/i)).toHaveLength(1)
  })
  it('renders the pending status distinctly and does not crash on null analysis', async () => {
    apiFetchMock.mockResolvedValue(detail({ status: 'pending', analysis: null, analysis_revision: null }))

    render(
      <Wrapper>
        <SupportCaseDetailPage />
      </Wrapper>,
    )

    await waitFor(() => expect(screen.getByText(/behandeling|pending/i)).toBeTruthy())
    // No review form when there is nothing analysed to review.
    expect(screen.queryByRole('button', { name: /beoordeling opslaan|save review/i })).toBeNull()
  })

  it('shows the conversation excerpt a finding cites', async () => {
    apiFetchMock.mockResolvedValue(detail())

    render(
      <Wrapper>
        <SupportCaseDetailPage />
      </Wrapper>,
    )

    await screen.findByText(/from the conversation|uit het gesprek/i)
    expect(screen.getByText('How do I export my invoices?')).toBeTruthy()
  })

  it('saves a review against the exact analysis_revision and refetches', async () => {
    apiFetchMock.mockImplementation((_path: string, init?: { method?: string }) => {
      if (init?.method === 'PATCH') {
        return Promise.resolve({
          analysis_revision: 'rev-abc',
          review: { decision: 'correct', note: 'looks right', reviewed_by: 'Reviewer', reviewed_at: '2026-09-11T00:00:00Z' },
        })
      }
      return Promise.resolve(detail())
    })

    render(
      <Wrapper>
        <SupportCaseDetailPage />
      </Wrapper>,
    )

    fireEvent.click(await screen.findByText(/the analysis is right|de analyse klopt$/i))
    fireEvent.click(screen.getByRole('button', { name: /beoordeling opslaan|save review/i }))

    await waitFor(() => {
      const patch = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'PATCH')
      expect(patch).toBeTruthy()
    })
    const patch = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'PATCH')!
    expect(String(patch[0])).toBe(
      '/api/app/knowledge-bases/company-kb/support-cases/7/findings/0/review',
    )
    expect(JSON.parse((patch[1] as { body: string }).body)).toEqual({
      analysis_revision: 'rev-abc',
      decision: 'correct',
      note: '',
    })
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
    // Refetch after save: the GET runs again.
    await waitFor(() =>
      expect(apiFetchMock.mock.calls.filter((c) => !(c[1] as { method?: string })?.method).length).toBeGreaterThan(1),
    )
  })

  it('shows a refresh prompt on a stale 409 and does not claim the review was saved', async () => {
    apiFetchMock.mockImplementation((_path: string, init?: { method?: string }) => {
      if (init?.method === 'PATCH') return Promise.reject(new ApiError(409, 'stale'))
      return Promise.resolve(detail())
    })

    render(
      <Wrapper>
        <SupportCaseDetailPage />
      </Wrapper>,
    )

    fireEvent.click(await screen.findByText(/the analysis is right|de analyse klopt$/i))
    fireEvent.click(screen.getByRole('button', { name: /beoordeling opslaan|save review/i }))

    await waitFor(() =>
      expect(screen.getByText(/changed since|gewijzigd sinds/i)).toBeTruthy(),
    )
    expect(toastSuccess).not.toHaveBeenCalled()
  })
  it('clears the previous judgement when refreshing a changed analysis', async () => {
    let changed = false
    apiFetchMock.mockImplementation((_path: string, init?: { method?: string }) => {
      if (init?.method === 'PATCH') {
        changed = true
        return Promise.reject(new ApiError(409, 'stale'))
      }
      return Promise.resolve(changed ? detail({
        analysis_revision: 'rev-new',
        analysis: [{ ...detail().analysis[0], question: 'A different customer question?' }],
      }) : detail())
    })
    render(<Wrapper><SupportCaseDetailPage /></Wrapper>)
    fireEvent.click(await screen.findByText(/the analysis is right|de analyse klopt$/i))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Judgement about the old question' } })
    fireEvent.click(screen.getByRole('button', { name: /beoordeling opslaan|save review/i }))
    fireEvent.click(await screen.findByRole('button', { name: /vernieuw|refresh/i }))
    await screen.findByText('A different customer question?')
    expect(screen.getByRole<HTMLTextAreaElement>('textbox').value).toBe('')
    expect(screen.getByRole('button', { name: /beoordeling opslaan|save review/i }).hasAttribute('disabled')).toBe(true)
  })

  it('re-runs analysis against the seen revision and surfaces a failed run as a failure', async () => {
    apiFetchMock.mockImplementation((_path: string, init?: { method?: string }) => {
      if (init?.method === 'POST') {
        return Promise.resolve({ case_id: '7', status: 'failed', changed: false, findings_count: 0 })
      }
      return Promise.resolve(detail())
    })

    render(
      <Wrapper>
        <SupportCaseDetailPage />
      </Wrapper>,
    )

    fireEvent.click(await screen.findByRole('button', { name: /re-run analysis|analyse opnieuw/i }))

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'POST')
      expect(post).toBeTruthy()
    })
    const post = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'POST')!
    expect(String(post[0])).toBe(
      '/api/app/knowledge-bases/company-kb/support-cases/7/reanalyze',
    )
    expect(JSON.parse((post[1] as { body: string }).body)).toEqual({ analysis_revision: 'rev-abc' })
    // Failed re-analysis is a visible failure, never a success toast.
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(toastSuccess).not.toHaveBeenCalled()
  })

  it('offers a diagnosis correction when a finding is judged wrong and sends it', async () => {
    apiFetchMock.mockImplementation((_path: string, init?: { method?: string }) => {
      if (init?.method === 'PATCH') {
        return Promise.resolve({
          analysis_revision: 'rev-abc',
          review: { decision: 'incorrect', note: '', corrected_diagnosis: 'incomplete', reviewed_by: 'R', reviewed_at: '2026-09-11T00:00:00Z' },
        })
      }
      return Promise.resolve(detail())
    })

    render(
      <Wrapper>
        <SupportCaseDetailPage />
      </Wrapper>,
    )

    fireEvent.click(await screen.findByText(/the analysis is wrong|de analyse klopt niet/i))
    // The correction control only appears once a finding is judged wrong.
    const correction = screen.getByLabelText(/correct the diagnosis|corrigeer de diagnose/i)
    fireEvent.change(correction, { target: { value: 'incomplete' } })
    fireEvent.click(screen.getByRole('button', { name: /beoordeling opslaan|save review/i }))

    await waitFor(() => {
      const patch = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'PATCH')
      expect(patch).toBeTruthy()
    })
    const patch = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'PATCH')!
    expect(JSON.parse((patch[1] as { body: string }).body)).toEqual({
      analysis_revision: 'rev-abc',
      decision: 'incorrect',
      note: '',
      corrected_diagnosis: 'incomplete',
    })
  })

  it('renders the analyser follow-through fields when present', async () => {
    apiFetchMock.mockResolvedValue(
      detail({
        analysis: [
          {
            ...detail().analysis[0],
            proposed_change: 'Add a step-by-step invoice export article.',
            comparison_limitations: ['Only three articles were in scope.'],
            search_queries: ['export invoices', 'download billing pdf'],
          },
        ],
      }),
    )

    render(
      <Wrapper>
        <SupportCaseDetailPage />
      </Wrapper>,
    )

    await screen.findByText('Add a step-by-step invoice export article.')
    expect(screen.getByText('Only three articles were in scope.')).toBeTruthy()
    expect(screen.getByText('download billing pdf')).toBeTruthy()
  })

})
