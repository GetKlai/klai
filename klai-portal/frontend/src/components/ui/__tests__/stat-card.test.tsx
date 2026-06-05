import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { StatCard } from '../stat-card'

describe('StatCard', () => {
  it('renders label, value and optional sub', () => {
    render(<StatCard label="Users" value={1284} sub="+38 this month" />)
    expect(screen.getByText('Users')).toBeTruthy()
    expect(screen.getByText('1284')).toBeTruthy()
    expect(screen.getByText('+38 this month')).toBeTruthy()
  })

  it('shows a dash for undefined value and a spinner while loading', () => {
    const { rerender, container } = render(<StatCard label="X" value={undefined} />)
    expect(screen.getByText('-')).toBeTruthy()
    rerender(<StatCard label="X" value={undefined} loading />)
    expect(container.querySelector('svg')).toBeTruthy()
  })

  it('renders as a button and fires onClick when provided', () => {
    const onClick = vi.fn()
    render(<StatCard label="Open" value={3} onClick={onClick} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalled()
  })

  it('renders a plain div (no button) without onClick', () => {
    render(<StatCard label="Plain" value={1} />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('applies the tone color to the value', () => {
    render(<StatCard label="Errors" value={2} tone="destructive" />)
    expect(screen.getByText('2').className).toContain('text-[var(--color-destructive)]')
  })

  it('uses the compact value size for size="sm"', () => {
    render(<StatCard size="sm" label="Bots" value={7} />)
    expect(screen.getByText('7').className).toContain('text-xl')
  })
})
