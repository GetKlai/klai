import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { profileLadderMessages } from './_messages'
import { ProfilePicker } from '../ProfilePicker'

vi.mock('@/paraglide/messages', () => ({ ...profileLadderMessages }))

describe('ProfilePicker', () => {
  it('renders all 5 ladder profiles in order', () => {
    render(<ProfilePicker value="" onChange={() => {}} />)
    const cards = screen.getAllByRole('radio')
    expect(cards).toHaveLength(5)
    expect(cards.map((c) => (c as HTMLInputElement).value)).toEqual([
      'personal',
      'company',
      'kb_manager',
      'group_manager',
      'admin',
    ])
  })

  it('shows description text on each card by default', () => {
    render(<ProfilePicker value="" onChange={() => {}} />)
    expect(screen.getByText('Personal description')).toBeTruthy()
    expect(screen.getByText('Admin description')).toBeTruthy()
  })

  it('hides description text in compact mode', () => {
    render(<ProfilePicker value="" onChange={() => {}} compact />)
    expect(screen.queryByText('Personal description')).toBeNull()
    expect(screen.getByText('Personal chat')).toBeTruthy()
  })

  it('marks the selected profile as checked', () => {
    render(<ProfilePicker value="kb_manager" onChange={() => {}} />)
    const radios = screen.getAllByRole('radio')
    expect((radios[2] as HTMLInputElement).checked).toBe(true)
    expect((radios[0] as HTMLInputElement).checked).toBe(false)
  })

  it('calls onChange with the role value when a card is clicked', () => {
    const onChange = vi.fn()
    render(<ProfilePicker value="" onChange={onChange} />)
    fireEvent.click(screen.getAllByRole('radio')[1])
    expect(onChange).toHaveBeenCalledWith('company')
  })

  it('blocks onChange and renders the disabled message when disabled', () => {
    const onChange = vi.fn()
    render(
      <ProfilePicker
        value="company"
        onChange={onChange}
        disabled
        disabledMessage="You cannot change your own profile."
      />,
    )
    fireEvent.click(screen.getAllByRole('radio')[3])
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText('You cannot change your own profile.')).toBeTruthy()
  })
})
