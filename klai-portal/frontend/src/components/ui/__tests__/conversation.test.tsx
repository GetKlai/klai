import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ConversationComposer, ConversationTimeline, type ConversationEntry } from '../conversation'

describe('ConversationComposer', () => {
  it('calls onSubmit when the send button is clicked with text present', () => {
    const onSubmit = vi.fn()
    render(
      <ConversationComposer value="Hallo team" onChange={() => {}} onSubmit={onSubmit} sendLabel="Verstuur" />,
    )
    fireEvent.click(screen.getByRole('button', { name: /verstuur/i }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('disables send and does not submit when the text is empty', () => {
    const onSubmit = vi.fn()
    render(<ConversationComposer value="   " onChange={() => {}} onSubmit={onSubmit} sendLabel="Verstuur" />)
    const button = screen.getByRole<HTMLButtonElement>('button', { name: /verstuur/i })
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('does not submit while a send is in flight', () => {
    const onSubmit = vi.fn()
    render(
      <ConversationComposer
        value="Hallo"
        onChange={() => {}}
        onSubmit={onSubmit}
        isSubmitting
        sendLabel="Verstuur"
      />,
    )
    const button = screen.getByRole<HTMLButtonElement>('button', { name: /verstuur/i })
    expect(button.disabled).toBe(true)
  })

  it('submits on Cmd/Ctrl + Enter', () => {
    const onSubmit = vi.fn()
    const { container } = render(
      <ConversationComposer value="Hallo" onChange={() => {}} onSubmit={onSubmit} sendLabel="Verstuur" />,
    )
    const textarea = container.querySelector('textarea')!
    fireEvent.keyDown(textarea, { key: 'Enter', metaKey: true })
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('does not submit on a bare Enter (newline)', () => {
    const onSubmit = vi.fn()
    const { container } = render(
      <ConversationComposer value="Hallo" onChange={() => {}} onSubmit={onSubmit} sendLabel="Verstuur" />,
    )
    const textarea = container.querySelector('textarea')!
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSubmit).not.toHaveBeenCalled()
  })
})

describe('ConversationTimeline', () => {
  const entries: ConversationEntry[] = [
    { id: 1, side: 'me', author: 'Jij', body: 'De zoekfunctie werkt niet.', at: '2026-06-04T09:12:00Z' },
    { id: 2, side: 'them', author: 'Klai team', body: 'We kijken ernaar.', at: '2026-06-04T09:40:00Z' },
    { type: 'system', id: 's1', label: 'Gemarkeerd als opgelost', at: '2026-06-05T11:03:30Z' },
    { id: 3, side: 'me', author: 'Jij', body: 'Top, bedankt!', at: '2026-06-05T11:20:00Z' },
  ]

  it('renders message bodies and the system line', () => {
    render(<ConversationTimeline entries={entries} locale="nl" />)
    expect(screen.getByText('De zoekfunctie werkt niet.')).toBeTruthy()
    expect(screen.getByText('We kijken ernaar.')).toBeTruthy()
    expect(screen.getByText('Gemarkeerd als opgelost')).toBeTruthy()
    expect(screen.getByText('Top, bedankt!')).toBeTruthy()
  })

  it('shows the empty label when there are no entries', () => {
    render(<ConversationTimeline entries={[]} locale="nl" emptyLabel="Nog geen gesprek" />)
    expect(screen.getByText('Nog geen gesprek')).toBeTruthy()
  })

  it('edits an editable own message via the inline editor (Cmd+Enter saves)', () => {
    const onEditMessage = vi.fn()
    const { container } = render(
      <ConversationTimeline
        entries={[{ id: 5, side: 'me', author: 'Jij', body: 'Hallo', at: '2026-06-05T11:20:00Z', editable: true }]}
        locale="nl"
        onEditMessage={onEditMessage}
      />,
    )
    expect(container.querySelector('textarea')).toBeNull()
    fireEvent.click(screen.getByRole('button'))
    const textarea = container.querySelector('textarea')!
    fireEvent.change(textarea, { target: { value: 'Aangepast bericht' } })
    fireEvent.keyDown(textarea, { key: 'Enter', metaKey: true })
    expect(onEditMessage).toHaveBeenCalledWith(5, 'Aangepast bericht')
  })

  it('shows no edit affordance without onEditMessage', () => {
    const { container } = render(
      <ConversationTimeline
        entries={[{ id: 6, side: 'me', author: 'Jij', body: 'x', at: '2026-06-05T11:20:00Z', editable: true }]}
        locale="nl"
      />,
    )
    expect(container.querySelector('button')).toBeNull()
  })

  it('shows no edit affordance for the other party', () => {
    const { container } = render(
      <ConversationTimeline
        entries={[{ id: 7, side: 'them', author: 'Klai team', body: 'x', at: '2026-06-05T11:20:00Z', editable: true }]}
        locale="nl"
        onEditMessage={() => {}}
      />,
    )
    expect(container.querySelector('button')).toBeNull()
  })
})
