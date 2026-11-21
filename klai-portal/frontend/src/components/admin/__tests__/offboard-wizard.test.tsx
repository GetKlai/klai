import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

const offboardMutate = vi.fn()
const deleteMutate = vi.fn()
vi.mock('@/hooks/useUserLifecycle', () => ({
  useOffboardUser: () => ({ mutate: offboardMutate, isPending: false }),
  useDeleteUserWithDispositions: () => ({ mutate: deleteMutate, isPending: false }),
}))

import { adminMessageMocks } from '@/routes/admin/_components/__tests__/_messages'
vi.mock('@/paraglide/messages', () => ({ ...adminMessageMocks }))

import { OffboardWizard } from '../offboard-wizard'

const users = [
  { zitadel_user_id: 'uid-leaving', email: 'leaving@example.com', first_name: 'Lee', last_name: 'Ving', role: 'admin', status: 'active', invite_pending: false },
  { zitadel_user_id: 'uid-admin', email: 'admin@example.com', first_name: 'Ada', last_name: 'Min', role: 'admin', status: 'active', invite_pending: false },
  { zitadel_user_id: 'uid-other-admin', email: 'other@example.com', first_name: 'Otto', last_name: 'Ther', role: 'admin', status: 'active', invite_pending: false },
  { zitadel_user_id: 'uid-member', email: 'member@example.com', first_name: 'Mem', last_name: 'Ber', role: 'company', status: 'active', invite_pending: false },
]

function mockPreview(apiKeys: number) {
  apiFetchMock.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/api/admin/users'
        ? { users }
        : {
            org_kbs_solely_owned: [],
            org_kbs_created: [],
            personal_kbs: [],
            api_keys_count: apiKeys,
            mcp_tokens_count: 1,
          },
    ),
  )
}

function renderWizard(mode: 'offboard' | 'delete') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <OffboardWizard
        userId="uid-leaving"
        userLabel="Lee Ving"
        currentAdminId="uid-admin"
        open
        onOpenChange={vi.fn()}
        mode={mode}
      />
    </QueryClientProvider>,
  )
}

function byTestId(id: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-test-id="${id}"]`)
  if (!el) throw new Error(`no element with data-test-id=${id}`)
  return el
}

function submit() {
  fireEvent.click(byTestId('offboard-submit-button')) // impact -> confirm
  fireEvent.click(byTestId('offboard-submit-button')) // confirm -> submit
}

beforeEach(() => {
  apiFetchMock.mockReset()
  offboardMutate.mockReset()
  deleteMutate.mockReset()
})

describe('OffboardWizard API key handover', () => {
  it('preselects the acting admin and offers only other admins as the new owner', async () => {
    mockPreview(2)
    renderWizard('offboard')

    const select = await screen.findByLabelText<HTMLSelectElement>('New owner of the API keys')
    expect(select.value).toBe('uid-admin')
    const options = Array.from(select.options).map((o) => o.value)
    expect(options).toEqual(['uid-admin', 'uid-other-admin'])
  })

  it('sends the chosen owner with the offboard request', async () => {
    mockPreview(2)
    renderWizard('offboard')

    const select = await screen.findByLabelText('New owner of the API keys')
    fireEvent.change(select, { target: { value: 'uid-other-admin' } })
    submit()

    expect(offboardMutate).toHaveBeenCalledTimes(1)
    expect(offboardMutate.mock.calls[0][0]).toMatchObject({
      userId: 'uid-leaving',
      api_keys_transfer_to: 'uid-other-admin',
    })
  })

  it('sends the acting admin as owner with the delete request by default', async () => {
    mockPreview(1)
    renderWizard('delete')

    await screen.findByLabelText('New owner of the API keys')
    submit()

    expect(deleteMutate.mock.calls[0][0]).toMatchObject({ api_keys_transfer_to: 'uid-admin' })
  })

  it('shows no owner choice when the person leaving has no API keys', async () => {
    mockPreview(0)
    renderWizard('offboard')

    await waitFor(() => expect(document.querySelector('[data-test-id="offboard-token-banner"]')).not.toBeNull())
    expect(screen.queryByLabelText('New owner of the API keys')).toBeNull()
  })
})
