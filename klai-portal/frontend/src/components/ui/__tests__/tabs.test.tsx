import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Settings } from 'lucide-react'
import { Tabs, type TabItem } from '../tabs'

const baseTabs: TabItem[] = [
  { id: 'details', label: 'Details' },
  { id: 'activity', label: 'Activity' },
]

describe('Tabs', () => {
  it('renders a tab per item inside a tablist', () => {
    render(<Tabs tabs={baseTabs} value="details" onValueChange={() => {}} />)
    expect(screen.getByRole('tablist')).toBeTruthy()
    expect(screen.getAllByRole('tab')).toHaveLength(2)
  })

  it('marks the active tab with aria-selected and the gray-900 underline', () => {
    render(<Tabs tabs={baseTabs} value="details" onValueChange={() => {}} />)
    const active = screen.getByRole('tab', { name: 'Details' })
    const inactive = screen.getByRole('tab', { name: 'Activity' })
    expect(active.getAttribute('aria-selected')).toBe('true')
    expect(inactive.getAttribute('aria-selected')).toBe('false')
    expect(active.className).toContain('border-gray-900')
    expect(inactive.className).toContain('border-transparent')
  })

  it('calls onValueChange with the tab id on click', () => {
    const onValueChange = vi.fn()
    render(<Tabs tabs={baseTabs} value="details" onValueChange={onValueChange} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }))
    expect(onValueChange).toHaveBeenCalledWith('activity')
  })

  it('shows the count badge only when count > 0, with the requested tone', () => {
    const { rerender } = render(
      <Tabs
        tabs={[
          { id: 'a', label: 'A', count: 0 },
          { id: 'b', label: 'B', count: 3, countTone: 'warning' },
        ]}
        value="a"
        onValueChange={() => {}}
      />
    )
    // count 0 → no badge; count 3 → visible
    expect(screen.queryByText('0')).toBeNull()
    const badge = screen.getByText('3')
    expect(badge.className).toContain('bg-[var(--color-warning)]')

    rerender(
      <Tabs
        tabs={[{ id: 'a', label: 'A', count: 5 }]}
        value="a"
        onValueChange={() => {}}
      />
    )
    // default tone is success
    expect(screen.getByText('5').className).toContain('bg-[var(--color-success)]')
  })

  it('renders an optional leading icon', () => {
    const { container } = render(
      <Tabs
        tabs={[{ id: 'a', label: 'A', icon: Settings }]}
        value="a"
        onValueChange={() => {}}
      />
    )
    expect(container.querySelector('svg')).toBeTruthy()
  })

  it('uses roving tabindex and activates on arrow keys', () => {
    const onValueChange = vi.fn()
    render(<Tabs tabs={baseTabs} value="details" onValueChange={onValueChange} />)
    const active = screen.getByRole('tab', { name: 'Details' })
    expect(active.getAttribute('tabindex')).toBe('0')
    expect(screen.getByRole('tab', { name: 'Activity' }).getAttribute('tabindex')).toBe('-1')
    fireEvent.keyDown(active, { key: 'ArrowRight' })
    expect(onValueChange).toHaveBeenCalledWith('activity')
    fireEvent.keyDown(active, { key: 'ArrowLeft' })
    expect(onValueChange).toHaveBeenCalledWith('activity') // wraps to last (also 'activity')
  })

  it('exposes an accessible label on the count badge via countLabel', () => {
    render(
      <Tabs
        tabs={[{ id: 'a', label: 'A', count: 3, countLabel: '3 unread' }]}
        value="a"
        onValueChange={() => {}}
      />
    )
    expect(screen.getByLabelText('3 unread').textContent).toBe('3')
  })
})
