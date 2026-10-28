import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}))
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

import { OrgKbChecklist } from '../OrgKbChecklist'

function renderChecklist(props: Partial<Parameters<typeof OrgKbChecklist>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onChange = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <OrgKbChecklist value={[1]} onChange={onChange} {...props} />
    </QueryClientProvider>,
  )
  return onChange
}

describe('OrgKbChecklist', () => {
  it('lists only organisation KBs and reports the new selection when one is toggled', async () => {
    apiFetchMock.mockResolvedValue({
      knowledge_bases: [
        { id: 1, name: 'Handbook', slug: 'handbook', owner_type: 'org' },
        { id: 2, name: 'Policies', slug: 'policies', owner_type: 'org' },
        { id: 3, name: 'Private notes', slug: 'private-notes', owner_type: 'user' },
      ],
    })
    const onChange = renderChecklist()

    await screen.findByText('Handbook')
    expect(screen.queryByText('Private notes')).toBeNull()
    expect(apiFetchMock).toHaveBeenCalledWith('/api/app/knowledge-bases?owner_type=org')

    const boxes = screen.getAllByRole<HTMLInputElement>('checkbox')
    expect(boxes.map((b) => b.checked)).toEqual([true, false])

    fireEvent.click(boxes[1])
    expect(onChange).toHaveBeenCalledWith([1, 2])
    fireEvent.click(boxes[0])
    expect(onChange).toHaveBeenCalledWith([])
  })
})
