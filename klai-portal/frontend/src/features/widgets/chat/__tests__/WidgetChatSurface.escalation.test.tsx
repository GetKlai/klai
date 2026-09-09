/**
 * Tests for the appointment-booking escalation button.
 *
 * Contract (backend-owned, consumed here):
 *   Streaming delta:     {"choices":[{"delta":{"escalation":{"appointment":true}}}]}
 *   Non-streaming:       message["escalation"] = {"appointment": true}
 *   Absent = no offer. Shape is exactly {"appointment": bool}.
 *
 * When an assistant message carries the signal, a button appears directly
 * under that message opening the booking URL (existing nerdsEnabled /
 * nerdsBookingUrl props) in a new tab. No signal, or no booking URL, means
 * no button and the message renders exactly as before.
 */
import type { ComponentProps } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('@/paraglide/runtime', () => ({
  getLocale: () => 'nl',
}))

vi.mock('@/paraglide/messages', () =>
  Object.fromEntries(
    [
      'widget_ai_disclaimer',
      'widget_chat_close',
      'widget_chat_copied',
      'widget_chat_default_empty_state',
      'widget_chat_input_placeholder',
      'widget_chat_meta_sources_many',
      'widget_chat_meta_sources_one',
      'widget_chat_new_conversation',
      'widget_chat_send',
      'widget_chat_share_link',
      'widget_chat_sources_label',
      'widget_chat_status_online',
      'widget_chat_typing',
      'widget_chat_book_appointment',
      'widget_chat_user_info_email',
      'widget_chat_user_info_help',
      'widget_chat_user_info_name',
    ].map((key) => [key, () => key]),
  ),
)

import { WidgetChatSurface } from '../WidgetChatSurface'

// jsdom does not implement scrollIntoView; the component calls it on every
// message-list update.
Element.prototype.scrollIntoView = vi.fn()

function renderSurface(props: Partial<ComponentProps<typeof WidgetChatSurface>> = {}) {
  return render(
    <WidgetChatSurface
      botName="Voys help-bot"
      chatEndpoint="/partner/v1/chat/completions"
      sessionToken="fake.jwt.token"
      {...props}
    />,
  )
}

/** SSE stream: a content delta, optionally an escalation delta, then [DONE]. */
function stubAssistantReply(content: string, appointment?: boolean) {
  const events: unknown[] = [{ choices: [{ delta: { content } }] }]
  if (appointment !== undefined) {
    events.push({ choices: [{ delta: { escalation: { appointment } } }] })
  }
  const lines = [...events.map((event) => `data: ${JSON.stringify(event)}\n\n`), 'data: [DONE]\n\n']
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) controller.enqueue(encoder.encode(line))
      controller.close()
    },
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Response(stream, { status: 200 })),
  )
}

function sendUserMessage(content: string) {
  const textbox = screen.getByPlaceholderText('widget_chat_input_placeholder')
  fireEvent.change(textbox, { target: { value: content } })
  fireEvent.keyDown(textbox, { key: 'Enter' })
}

const BOOKING_URL_WITH_QUERY = 'https://support.voys.nl/book/voys?t=abc'
const BOOKING_URL_BARE = 'https://support.voys.nl/agenda/abc/workflow/voys'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('WidgetChatSurface appointment escalation', () => {
  it('shows the booking button under the assistant message that carries the signal', async () => {
    stubAssistantReply('Dat kan ik helaas niet volledig beantwoorden.', true)
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: BOOKING_URL_WITH_QUERY })

    sendUserMessage('Kun je warm doorverbinden?')

    const button = await screen.findByRole('link', { name: 'widget_chat_book_appointment' })
    expect(button.getAttribute('target')).toBe('_blank')
    expect(button.getAttribute('rel')).toBe('noopener noreferrer')
    expect(button.getAttribute('href')).toBe(`${BOOKING_URL_WITH_QUERY}&embed=1&lng=nl`)
  })

  it('does not show a button when the reply carries no escalation signal', async () => {
    stubAssistantReply('Hier is het antwoord op je vraag.')
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: BOOKING_URL_WITH_QUERY })

    sendUserMessage('Wat zijn de openingstijden?')

    await screen.findByText('Hier is het antwoord op je vraag.')
    expect(screen.queryByRole('link', { name: 'widget_chat_book_appointment' })).toBeNull()
  })

  it('does not show a button when the signal explicitly says no offer', async () => {
    stubAssistantReply('Hier is het antwoord op je vraag.', false)
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: BOOKING_URL_WITH_QUERY })

    sendUserMessage('Wat zijn de openingstijden?')

    await screen.findByText('Hier is het antwoord op je vraag.')
    expect(screen.queryByRole('link', { name: 'widget_chat_book_appointment' })).toBeNull()
  })

  it('does not show a button when the signal is present but no booking URL is configured', async () => {
    stubAssistantReply('Dat kan ik helaas niet volledig beantwoorden.', true)
    renderSurface({ nerdsEnabled: false, nerdsBookingUrl: '' })

    sendUserMessage('Kun je warm doorverbinden?')

    await screen.findByText('Dat kan ik helaas niet volledig beantwoorden.')
    expect(screen.queryByRole('link', { name: 'widget_chat_book_appointment' })).toBeNull()
  })

  it('starts the query string when the booking URL has none (same separator rule as the footer link)', async () => {
    stubAssistantReply('Dat kan ik helaas niet volledig beantwoorden.', true)
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: BOOKING_URL_BARE })

    sendUserMessage('Kun je warm doorverbinden?')

    const button = await screen.findByRole('link', { name: 'widget_chat_book_appointment' })
    expect(button.getAttribute('href')).toBe(`${BOOKING_URL_BARE}?embed=1&lng=nl`)
  })
})
