import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { MultiSelect, type MultiSelectOption } from '../multi-select'

const options: MultiSelectOption[] = [
  { value: 'kb', label: 'Kennisbank' },
  { value: 'chat', label: 'Chat' },
]

function MultiSelectHarness() {
  const [value, setValue] = useState(['kb', 'chat'])

  return <MultiSelect options={options} value={value} onChange={setValue} />
}

function SingleMultiSelectHarness() {
  const [value, setValue] = useState(['kb'])

  return <MultiSelect options={options} value={value} onChange={setValue} />
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  })
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  })
})

afterAll(() => {
  vi.unstubAllGlobals()
  delete (Element.prototype as Partial<Element>).scrollIntoView
})

describe('MultiSelect', () => {
  it('renders the native trigger and chip remove buttons as siblings', () => {
    render(<MultiSelectHarness />)

    const trigger = screen.getByRole('button', { name: 'Kennisbank, Chat' })
    const remove = screen.getByRole('button', { name: 'Verwijder Kennisbank' })

    expect(trigger.tagName).toBe('BUTTON')
    expect(trigger.tabIndex).toBe(0)
    expect(remove.tagName).toBe('BUTTON')
    expect(remove.tabIndex).toBe(0)
    expect(trigger.contains(remove)).toBe(false)
  })

  it('opens from the field trigger and removes a chip without opening', () => {
    const { unmount } = render(<MultiSelectHarness />)

    const trigger = screen.getByRole('button', { name: 'Kennisbank, Chat' })
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')

    unmount()
    render(<MultiSelectHarness />)

    fireEvent.click(screen.getByRole('button', { name: 'Verwijder Kennisbank' }))

    expect(screen.queryByRole('button', { name: 'Verwijder Kennisbank' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Chat' }).getAttribute('aria-expanded')).toBe('false')
  })

  it('moves focus to the next remove button after removing a chip', async () => {
    render(<MultiSelectHarness />)

    const firstRemove = screen.getByRole('button', { name: 'Verwijder Kennisbank' })
    firstRemove.focus()
    fireEvent.click(firstRemove)

    const nextRemove = screen.getByRole('button', { name: 'Verwijder Chat' })
    await waitFor(() => expect(document.activeElement).toBe(nextRemove))
  })

  it('returns focus to the trigger after removing the last chip', async () => {
    render(<SingleMultiSelectHarness />)

    const remove = screen.getByRole('button', { name: 'Verwijder Kennisbank' })
    remove.focus()
    fireEvent.click(remove)

    const trigger = screen.getByRole('button', { name: 'Selecteer...' })
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })
})
