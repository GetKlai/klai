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
  it('shows type as a table column and keeps personal knowledge bases above organization ones', () => {
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

    expect(screen.getByRole('columnheader', { name: /type/i })).toBeTruthy()
    const rows = screen.getAllByRole('row').slice(1)
    const firstCells = within(rows[0]).getAllByRole('cell')
    const secondCells = within(rows[1]).getAllByRole('cell')
    const thirdCells = within(rows[2]).getAllByRole('cell')

    expect(firstCells[0].textContent).toBe('My private KB')
    expect(firstCells[1].textContent).toMatch(/persoonlijk|personal/i)
    expect(secondCells[0].textContent).toBe('Org handbook')
    expect(secondCells[1].textContent).toMatch(/organisatie|organization/i)
    expect(thirdCells[0].textContent).toBe('Org policies')
    expect(thirdCells[1].textContent).toMatch(/organisatie|organization/i)
  })
})
