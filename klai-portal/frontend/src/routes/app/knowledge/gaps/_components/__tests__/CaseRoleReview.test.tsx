/**
 * Speaker-role correction for call evidence (support-gap-detection.md, gap
 * quality review): a call whose segments have no confirmed speaker reads as
 * "needs role review" with the timestamp and source text preserved; the
 * reviewer sets roles per segment or in bulk per speaker, and the save carries
 * ONLY the segments they changed against the analysis revision they saw. A
 * stale (409) save is shown as a refresh prompt, never as a success.
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

import { CaseRoleReview } from '../CaseRoleReview'
import { ApiError } from '@/lib/apiFetch'
import type { CaseMessage } from '../../-support-helpers'

function segment(overrides: Partial<CaseMessage> = {}): CaseMessage {
  return {
    id: 'seg-1',
    kind: 'segment',
    role: 'unknown',
    text: 'I cannot log in to the portal.',
    occurred_at: null,
    visibility: 'unknown',
    start_seconds: 0,
    end_seconds: 4,
    medium: 'audio',
    thread_id: null,
    reply_to_id: null,
    speaker_id: 'A',
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
  analysisRevision: 'rev-abc',
}

beforeEach(() => {
  apiFetchMock.mockReset()
  toastSuccess.mockReset()
  toastError.mockReset()
})

describe('CaseRoleReview', () => {
  it('reports a failed analysis after saving roles without claiming success', async () => {
    apiFetchMock.mockResolvedValue({ case_id: '7', status: 'failed', changed: true, findings_count: 0 })
    render(<Wrapper><CaseRoleReview {...baseProps} messages={[segment()]} /></Wrapper>)
    fireEvent.change(screen.getByLabelText(/role for this segment|rol voor dit segment/i), {
      target: { value: 'customer' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save roles|rollen opslaan/i }))
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(toastSuccess).not.toHaveBeenCalled()
  })
  it('refreshes saved roles when analysis returns 503', async () => {
    apiFetchMock.mockRejectedValue(new ApiError(503, 'analysis_failed'))
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    render(<QueryClientProvider client={client}><CaseRoleReview {...baseProps} messages={[segment()]} /></QueryClientProvider>)
    fireEvent.change(screen.getByLabelText(/role for this segment|rol voor dit segment/i), { target: { value: 'customer' } })
    fireEvent.click(screen.getByRole('button', { name: /save roles|rollen opslaan/i }))
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(invalidate).toHaveBeenCalled()
    expect(toastSuccess).not.toHaveBeenCalled()
  })
  it('flags unknown-role segments and preserves timestamp and source text', () => {
    render(
      <Wrapper>
        <CaseRoleReview {...baseProps} messages={[segment()]} />
      </Wrapper>,
    )
    expect(screen.getByText(/needs role review|rol-review nodig/i)).toBeTruthy()
    expect(screen.getByText('I cannot log in to the portal.')).toBeTruthy()
    // Timestamp is kept visible (0–4s), not overwritten by the editor.
    expect(screen.getByText(/0.*4/)).toBeTruthy()
  })

  it('applies a bulk speaker role and saves only the changed segments', async () => {
    apiFetchMock.mockResolvedValue({ case_id: '7', status: 'analyzed', changed: true, findings_count: 2 })
    const messages = [
      segment({ id: 'seg-1', speaker_id: 'A' }),
      segment({ id: 'seg-2', speaker_id: 'A', text: 'Second thing from A.' }),
      segment({ id: 'seg-3', speaker_id: 'B', text: 'Agent reply.' }),
    ]
    render(
      <Wrapper>
        <CaseRoleReview {...baseProps} messages={messages} />
      </Wrapper>,
    )

    // Bulk control for speaker A → customer.
    const bulkSelect = screen.getByLabelText(/speaker a|spreker a/i)
    fireEvent.change(bulkSelect, { target: { value: 'customer' } })
    fireEvent.click(screen.getAllByRole('button', { name: /apply|toepassen/i })[0])

    fireEvent.click(screen.getByRole('button', { name: /save roles|rollen opslaan/i }))

    await waitFor(() => {
      const patch = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'PATCH')
      expect(patch).toBeTruthy()
    })
    const patch = apiFetchMock.mock.calls.find((c) => (c[1] as { method?: string })?.method === 'PATCH')!
    expect(String(patch[0])).toBe(
      '/api/app/knowledge-bases/company-kb/support-cases/7/roles',
    )
    const body = JSON.parse((patch[1] as { body: string }).body)
    expect(body.analysis_revision).toBe('rev-abc')
    // Only the two speaker-A segments were changed; seg-3 stays untouched.
    expect(body.message_roles).toEqual({ 'seg-1': 'customer', 'seg-2': 'customer' })
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
  })

  it('has no save until something changes and shows the stale prompt on 409', async () => {
    apiFetchMock.mockRejectedValue(new ApiError(409, 'stale'))
    render(
      <Wrapper>
        <CaseRoleReview {...baseProps} messages={[segment({ speaker_id: null })]} />
      </Wrapper>,
    )
    // No speaker id → no bulk control, per-segment only.
    expect(screen.queryByText(/set every segment|stel elk segment/i)).toBeNull()

    const save = screen.getByRole('button', { name: /save roles|rollen opslaan/i })
    expect(save.hasAttribute('disabled')).toBe(true)

    fireEvent.change(screen.getByLabelText(/role for this segment|rol voor dit segment/i), {
      target: { value: 'agent' },
    })
    expect(save.hasAttribute('disabled')).toBe(false)
    fireEvent.click(save)

    await waitFor(() => expect(screen.getByText(/changed since|gewijzigd sinds/i)).toBeTruthy())
    expect(toastSuccess).not.toHaveBeenCalled()
  })
})
