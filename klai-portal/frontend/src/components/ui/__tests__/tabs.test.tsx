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

  it('shows neutral count badges only when count > 0', () => {
    const { rerender } = render(
      <Tabs
        tabs={[
          { id: 'a', label: 'A', count: 0 },
          { id: 'b', label: 'B', count: 3 },
        ]}
        value="a"
        onValueChange={() => {}}
      />
    )
    // count 0 → no badge; count 3 → visible
    expect(screen.queryByText('0')).toBeNull()
    const badge = screen.getByText('3')
    expect(badge.className).toContain('bg-gray-100')
    expect(badge.className).toContain('text-gray-600')

    rerender(
      <Tabs
        tabs={[{ id: 'a', label: 'A', count: 5 }]}
        value="a"
        onValueChange={() => {}}
      />
    )
    expect(screen.getByText('5').className).toContain('bg-gray-100')
  })

  it('uses success for notification badges by default and allows semantic alert tones', () => {
    const { rerender } = render(
      <Tabs
        tabs={[{ id: 'a', label: 'A', notificationCount: 5 }]}
        value="a"
        onValueChange={() => {}}
      />
    )
    expect(screen.getByText('5').className).toContain('bg-[var(--color-success)]')

    rerender(
      <Tabs
        tabs={[{ id: 'a', label: 'A', notificationCount: 2, notificationTone: 'destructive' }]}
        value="a"
        onValueChange={() => {}}
      />
    )
    expect(screen.getByText('2').className).toContain('bg-[var(--color-destructive)]')
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

  it('exposes an accessible label on a notification badge via notificationLabel', () => {
    render(
      <Tabs
        tabs={[{ id: 'a', label: 'A', notificationCount: 3, notificationLabel: '3 unread' }]}
        value="a"
        onValueChange={() => {}}
      />
    )
    expect(screen.getByLabelText('3 unread').textContent).toBe('3')
  })
})
