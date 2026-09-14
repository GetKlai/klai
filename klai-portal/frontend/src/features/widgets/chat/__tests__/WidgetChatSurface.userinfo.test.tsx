/**
 * Tests for the pre-chat step that asks the visitor for a name and an e-mail.
 *
 * Two things it must get right. The visitor sees why we want the address
 * before the conversation starts, and can decline without getting stuck. And
 * whatever they leave travels as its own request field: putting it in the
 * message content used to send the address to the model and made the stored
 * first question read "Visitor details: …" instead of the actual question.
 */
import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

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
      'widget_chat_user_info_email',
      'widget_chat_user_info_help',
      'widget_chat_user_info_name',
      'widget_chat_user_info_skip',
      'widget_chat_user_info_start',
      'widget_chat_user_info_title',
    ].map((key) => [key, () => key]),
  ),
)

import { WidgetChatSurface } from '../WidgetChatSurface'

function renderSurface(props: Partial<ComponentProps<typeof WidgetChatSurface>> = {}) {
  return render(
    <WidgetChatSurface
      botName="Voys help-bot"
      chatEndpoint="/partner/v1/chat/completions"
      sessionToken="fake.jwt.token"
      collectUserInfo
      {...props}
    />,
  )
}

/** A finished SSE response with one short answer. */
function stubChatEndpoint() {
  const fetchMock = vi.fn(() => Promise.resolve({
    ok: true,
    body: {
      getReader: () => {
        let done = false
        return {
          read: () => {
            if (done) return Promise.resolve({ value: undefined, done: true })
            done = true
            return Promise.resolve({
              value: new TextEncoder().encode(
                'data: {"choices":[{"delta":{"content":"Dag Mark"}}]}\n\n',
              ),
              done: false,
            })
          },
        }
      },
    },
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function requestBody(fetchMock: ReturnType<typeof stubChatEndpoint>) {
  const [, init] = fetchMock.mock.calls[0] as unknown as [string, { body: string }]
  return JSON.parse(init.body) as {
    visitor_name?: string
    visitor_email?: string
    messages: { role: string; content: string }[]
  }
}

describe('WidgetChatSurface pre-chat step', () => {
  beforeEach(() => {
    // jsdom has no layout, so the surface's auto-scroll effect would throw.
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('asks before the conversation starts and hides the composer until answered', () => {
    renderSurface()

    expect(screen.getByText('widget_chat_user_info_title')).toBeTruthy()
    expect(screen.getByText('widget_chat_user_info_help')).toBeTruthy()
    expect(screen.queryByPlaceholderText('widget_chat_input_placeholder')).toBeNull()
  })

  it('sends name and e-mail as their own fields, leaving the question untouched', async () => {
    const fetchMock = stubChatEndpoint()
    renderSurface()

    fireEvent.change(screen.getByPlaceholderText('widget_chat_user_info_name'), {
      target: { value: 'Mark' },
    })
    fireEvent.change(screen.getByPlaceholderText('widget_chat_user_info_email'), {
      target: { value: 'mark@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'widget_chat_user_info_start' }))

    const composer = screen.getByPlaceholderText('widget_chat_input_placeholder')
    fireEvent.change(composer, { target: { value: 'Hoe zeg ik mijn abonnement op?' } })
    fireEvent.click(screen.getByRole('button', { name: 'widget_chat_send' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const body = requestBody(fetchMock)
    expect(body.visitor_name).toBe('Mark')
    expect(body.visitor_email).toBe('mark@example.com')
    expect(body.messages).toEqual([
      { role: 'user', content: 'Hoe zeg ik mijn abonnement op?' },
    ])
  })

  it('lets the visitor decline and then chat without contact details', async () => {
    const fetchMock = stubChatEndpoint()
    renderSurface()

    fireEvent.click(screen.getByRole('button', { name: 'widget_chat_user_info_skip' }))

    const composer = screen.getByPlaceholderText('widget_chat_input_placeholder')
    fireEvent.change(composer, { target: { value: 'Even een vraag' } })
    fireEvent.click(screen.getByRole('button', { name: 'widget_chat_send' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const body = requestBody(fetchMock)
    expect(body.visitor_name).toBeUndefined()
    expect(body.visitor_email).toBeUndefined()
  })

  it('shows the normal empty state when the widget does not ask for details', () => {
    renderSurface({ collectUserInfo: false })

    expect(screen.queryByText('widget_chat_user_info_title')).toBeNull()
    expect(screen.getByPlaceholderText('widget_chat_input_placeholder')).toBeTruthy()
  })
})
