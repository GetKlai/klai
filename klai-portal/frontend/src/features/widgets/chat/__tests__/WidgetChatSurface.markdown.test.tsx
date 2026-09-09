/**
 * Tests for assistant markdown rendering and the XSS boundary around it.
 *
 * Assistant replies render through react-markdown (already used elsewhere
 * in this package — src/routes/app/meetings/$meetingId.tsx and
 * src/routes/app/transcribe/$transcriptionId.tsx) so bold, italic, and
 * lists show as formatted HTML instead of literal `**`/`*`/`-` characters.
 * No rehype-raw or similar plugin is added: react-markdown parses markdown
 * into React elements and never interprets raw HTML found in the source by
 * default, so there is no HTML string to sanitize and no
 * dangerouslySetInnerHTML in the path — raw HTML in either an assistant or
 * a user message shows up as literal escaped text, never as a real element.
 * User messages additionally never reach the markdown parser at all — they
 * render through a plain JSX text node, unconditionally.
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

/** One SSE stream carrying a single content delta, followed by [DONE]. */
function stubAssistantReply(content: string) {
  const lines = [
    `data: ${JSON.stringify({ choices: [{ delta: { content } }] })}\n\n`,
    'data: [DONE]\n\n',
  ]
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

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('WidgetChatSurface assistant markdown', () => {
  it('renders bold, italic and a list as formatted HTML, not literal markdown', async () => {
    stubAssistantReply('**Stap 1:** doe *dit* nu.\n\n- Eerste punt\n- Tweede punt')
    renderSurface()

    sendUserMessage('Hoe werkt het?')

    const strong = await screen.findByText('Stap 1:')
    expect(strong.tagName).toBe('STRONG')
    expect(screen.getByText('dit').tagName).toBe('EM')

    const list = document.querySelector('ul')
    expect(list).not.toBeNull()
    const items = list ? Array.from(list.querySelectorAll('li')).map((li) => li.textContent) : []
    expect(items).toEqual(['Eerste punt', 'Tweede punt'])

    // The literal markdown syntax must not remain in the rendered text.
    expect(screen.queryByText(/\*\*Stap 1:\*\*/)).toBeNull()
  })

  it('never turns raw HTML embedded in an assistant reply into a real element', async () => {
    // No rehype-raw plugin is added to <Markdown>, so this is the guarantee
    // that actually matters now: react-markdown escapes raw HTML found in
    // the markdown source into literal text instead of parsing it.
    const sentence = 'Zie dit voorbeeld: <img src=x onerror=alert(1)> en niets gebeurt.'
    const alertSpy = vi.fn()
    vi.stubGlobal('alert', alertSpy)
    stubAssistantReply(sentence)
    renderSurface()

    sendUserMessage('Kun je een voorbeeld geven?')

    await screen.findByText(sentence)
    expect(document.querySelector('img')).toBeNull()
    expect(alertSpy).not.toHaveBeenCalled()
  })
})

describe('WidgetChatSurface user message safety', () => {
  it('renders a user message containing HTML as inert text and executes nothing', async () => {
    const alertSpy = vi.fn()
    vi.stubGlobal('alert', alertSpy)
    stubAssistantReply('Bedankt voor je bericht.')
    renderSurface()

    const payload = '<img src=x onerror=alert(1)>'
    sendUserMessage(payload)

    // The payload shows up verbatim as text content...
    await screen.findByText(payload)
    // ...and was never parsed into a real <img> element.
    expect(document.querySelector('img')).toBeNull()
    expect(alertSpy).not.toHaveBeenCalled()
  })
})
