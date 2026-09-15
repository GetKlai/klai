import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ConversationTranscript, QualityPanel } from '..'
import type { ConversationMessage, ConversationQuality } from '..'

// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the transcript and judge panel move out of
// the two admin drawers into one shared feature. These tests pin the two
// behaviours that were only guaranteed by the copies: the source URL scheme
// allowlist (REQ-9) and the outcome → Badge semantic variant mapping (REQ-3).

function message(overrides: Partial<ConversationMessage> = {}): ConversationMessage {
  return {
    id: 1,
    role: 'user',
    content: 'Wat is jullie retourbeleid?',
    sources: null,
    created_at: '2026-09-01T09:00:00Z',
    sequence: 1,
    rating: null,
    ...overrides,
  }
}

function quality(overrides: Partial<ConversationQuality> = {}): ConversationQuality {
  return {
    outcome: 'resolved',
    failure_category: null,
    reasoning: null,
    confidence: null,
    suggested_action: null,
    judged_at: null,
    ...overrides,
  }
}

describe('ConversationTranscript', () => {
  it('links only http(s) sources and renders unsafe schemes as plain text', () => {
    const { container } = render(
      <ConversationTranscript
        messages={[
          message(),
          message({
            id: 2,
            role: 'assistant',
            content: 'Retour kan binnen 30 dagen.',
            sequence: 2,
            sources: [
              { label: '1', title: 'Retourbeleid', url: 'https://example.com/retour' },
              { label: '2', title: 'Kwetsbaar', url: 'javascript:alert(1)' },
            ],
          }),
        ]}
      />,
    )

    const safe = screen.getByText('Retourbeleid').closest('a')
    expect(safe?.getAttribute('href')).toBe('https://example.com/retour')
    expect(safe?.getAttribute('rel')).toContain('noopener')
    expect(container.querySelector('a[href="javascript:alert(1)"]')).toBeNull()
    expect(screen.getByText('Kwetsbaar').closest('a')).toBeNull()
  })

  it('shows the thumbs-down mark on an assistant message rated thumbsDown', () => {
    render(
      <ConversationTranscript
        messages={[
          message({ id: 2, role: 'assistant', content: 'Retour kan niet.', sequence: 2, rating: 'thumbsDown' }),
        ]}
      />,
    )

    expect(
      screen.getByRole('img', { name: 'Door klant beoordeeld met duim omlaag' }),
    ).toBeTruthy()
    expect(
      screen.queryByRole('img', { name: 'Door klant beoordeeld met duim omhoog' }),
    ).toBeNull()
  })
})

describe('QualityPanel', () => {
  it('maps outcome to the existing badge variants and shows judge text', () => {
    const { rerender } = render(
      <QualityPanel
        quality={quality({
          outcome: 'resolved',
          reasoning: 'Antwoord dekt de vraag volledig.',
          suggested_action: 'Geen actie nodig.',
        })}
      />,
    )

    const resolved = screen.getByText('resolved')
    expect(resolved.className).toContain('var(--color-success-text)')
    expect(screen.getByText('Antwoord dekt de vraag volledig.')).toBeTruthy()
    expect(screen.getByText('Geen actie nodig.')).toBeTruthy()

    rerender(<QualityPanel quality={quality({ outcome: 'unresolved' })} />)

    const unresolved = screen.getByText('unresolved')
    expect(unresolved.className).not.toContain('var(--color-success-text)')
    expect(unresolved.className).toContain('bg-gray-100')
    expect(screen.queryByText('Antwoord dekt de vraag volledig.')).toBeNull()
    expect(screen.queryByText('Geen actie nodig.')).toBeNull()
  })
})
