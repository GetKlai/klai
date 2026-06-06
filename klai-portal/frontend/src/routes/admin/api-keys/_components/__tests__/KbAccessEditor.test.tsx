import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { OrgKnowledgeBase } from '../../-types'

const useOrgKnowledgeBasesMock = vi.fn()

vi.mock('../../-hooks', () => ({
  useOrgKnowledgeBases: () => useOrgKnowledgeBasesMock(),
}))

import { KbAccessEditor } from '../KbAccessEditor'

function kb(id: number, name: string, ownerType: string): OrgKnowledgeBase {
  return {
    id,
    name,
    slug: name.toLowerCase().replaceAll(' ', '-'),
    owner_type: ownerType,
  }
}

describe('KbAccessEditor', () => {
  it('shows knowledge base scope and keeps organization knowledge bases above personal ones', () => {
    useOrgKnowledgeBasesMock.mockReturnValue({
      isLoading: false,
      data: {
        knowledge_bases: [
          kb(1, 'My private KB', 'user'),
          kb(2, 'Org handbook', 'org'),
          kb(3, 'Org policies', 'org'),
        ],
      },
    })

    render(
      <KbAccessEditor
        value={[]}
        onChange={vi.fn()}
        knowledgeAppendEnabled={true}
      />,
    )

    const rows = screen.getAllByRole('row').slice(1)

    expect(within(rows[0]).getByText('Org handbook')).toBeTruthy()
    expect(within(rows[0]).getByText(/organisatie|organization/i)).toBeTruthy()
    expect(within(rows[1]).getByText('Org policies')).toBeTruthy()
    expect(within(rows[1]).getByText(/organisatie|organization/i)).toBeTruthy()
    expect(within(rows[2]).getByText('My private KB')).toBeTruthy()
    expect(within(rows[2]).getByText(/persoonlijk|personal/i)).toBeTruthy()
  })
})
