import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Switch } from '../switch'

describe('Switch', () => {
  it('renders as an accessible switch with checked state', () => {
    render(<Switch checked onCheckedChange={() => {}} aria-label="Enable feature" />)

    const control = screen.getByRole('switch', { name: 'Enable feature' })
    expect(control.getAttribute('aria-checked')).toBe('true')
  })

  it('calls onCheckedChange with the next state on click', () => {
    const onCheckedChange = vi.fn()
    render(<Switch checked={false} onCheckedChange={onCheckedChange} aria-label="Enable feature" />)

    fireEvent.click(screen.getByRole('switch', { name: 'Enable feature' }))
    expect(onCheckedChange).toHaveBeenCalledWith(true)
  })
})
