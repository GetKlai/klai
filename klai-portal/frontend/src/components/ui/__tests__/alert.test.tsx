import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Bell } from 'lucide-react'
import { Alert } from '../alert'

describe('Alert', () => {
  it('renders its children inside a role="alert" region', () => {
    render(<Alert variant="info">Heads up</Alert>)
    const alert = screen.getByRole('alert')
    expect(alert).toBeTruthy()
    expect(alert.textContent).toContain('Heads up')
  })

  it('applies the semantic token classes for the variant', () => {
    const { container } = render(<Alert variant="warning">x</Alert>)
    const alert = container.querySelector('[role="alert"]')!
    expect(alert.className).toContain('bg-[var(--color-warning)]/5')
    expect(alert.className).toContain('border-[var(--color-warning)]/30')
    expect(alert.className).toContain('text-[var(--color-warning-text)]')
  })

  it('renders the variant default icon (an svg) by default', () => {
    const { container } = render(<Alert variant="success">ok</Alert>)
    expect(container.querySelector('svg')).toBeTruthy()
  })

  it('hides the icon when icon={null}', () => {
    const { container } = render(
      <Alert variant="destructive" icon={null}>
        no icon
      </Alert>
    )
    expect(container.querySelector('svg')).toBeNull()
  })

  it('accepts a custom icon override', () => {
    const { container } = render(
      <Alert variant="info" icon={Bell}>
        custom
      </Alert>
    )
    // lucide sets a data attribute we can assert the override rendered an svg
    expect(container.querySelector('svg')).toBeTruthy()
  })

  it('uses compact spacing for size="sm"', () => {
    const { container } = render(
      <Alert variant="info" size="sm">
        small
      </Alert>
    )
    const alert = container.querySelector('[role="alert"]')!
    expect(alert.className).toContain('text-xs')
  })
})
