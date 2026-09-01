import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

describe('Field', () => {
  it('generates an id and associates the label with its control', () => {
    render(
      <Field label="Email">
        <Input type="email" />
      </Field>,
    )

    const input = screen.getByLabelText('Email')
    expect(input.id).not.toBe('')
    expect(screen.getByText('Email').getAttribute('for')).toBe(input.id)
  })

  it('clones an explicit id while preserving input props', () => {
    render(
      <Field id="verification-code" label="Code">
        <Input
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={6}
          autoComplete="one-time-code"
          autoFocus
          required
        />
      </Field>,
    )

    const input = screen.getByLabelText('Code')
    expect(input.getAttribute('id')).toBe('verification-code')
    expect(input.getAttribute('type')).toBe('text')
    expect(input.getAttribute('inputmode')).toBe('numeric')
    expect(input.getAttribute('pattern')).toBe('[0-9]*')
    expect(input.getAttribute('maxlength')).toBe('6')
    expect(input.getAttribute('autocomplete')).toBe('one-time-code')
    expect(document.activeElement).toBe(input)
    expect((input as HTMLInputElement).required).toBe(true)
  })

  it('associates hint and error feedback with the control', () => {
    const { rerender } = render(
      <Field id="password" label="Password" hint="Use at least 12 characters">
        <Input type="password" aria-describedby="policy" />
      </Field>,
    )

    const input = screen.getByLabelText('Password')
    expect(input.getAttribute('aria-describedby')).toBe(
      'policy password-description',
    )
    expect(input.hasAttribute('aria-invalid')).toBe(false)

    rerender(
      <Field id="password" label="Password" error="Password is too short">
        <Input type="password" />
      </Field>,
    )

    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByRole('alert').textContent).toBe('Password is too short')
  })
})
