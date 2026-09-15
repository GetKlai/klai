/**
 * Tests for the in-page link panel on the public share surface.
 *
 * The embeddable widget has diverted footer links into an iframe panel over
 * the chat since footer_links_in_widget exists; this surface (the /bot/<id>
 * share page) must behave the same way for the same config flag. A plain
 * left-click on an absolute http(s) footer link is captured into the panel,
 * while modifier clicks keep the anchor's own new-tab behaviour — and the
 * panel head always carries a new-tab link of its own, because a page that
 * refuses to be framed (X-Frame-Options / CSP) still fires `load` on the
 * browser's error page, so no load event can carry that fallback.
 */
import type { ComponentProps } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'

vi.mock('@/paraglide/runtime', () => ({
  getLocale: () => 'nl',
}))

vi.mock('@/paraglide/messages', () =>
  Object.fromEntries(
    [
      'widget_ai_disclaimer',
      'widget_chat_book_appointment',
      'widget_chat_close',
      'widget_chat_copied',
      'widget_chat_default_empty_state',
      'widget_chat_input_placeholder',
      'widget_chat_new_conversation',
      'widget_chat_panel_back',
      'widget_chat_panel_open_new_tab',
      'widget_chat_send',
      'widget_chat_share_link',
      'widget_chat_sources_label',
      'widget_chat_status_online',
      'widget_chat_typing',
    ].map((key) => [key, () => key]),
  ),
)

import { WidgetChatSurface } from '../WidgetChatSurface'

// jsdom does not implement scrollIntoView; the component calls it on every
// message-list update.
Element.prototype.scrollIntoView = vi.fn()

const FOOTER_TEXT = 'Plan een afspraak met [onze nerds](https://example.com/agenda).'

function renderSurface(props: Partial<ComponentProps<typeof WidgetChatSurface>> = {}) {
  return render(
    <WidgetChatSurface
      botName="Voys help-bot"
      chatEndpoint="/partner/v1/chat/completions"
      sessionToken="fake.jwt.token"
      footerText={FOOTER_TEXT}
      {...props}
    />,
  )
}

/**
 * Dispatch a real, cancelable click on the footer anchor and hand back the
 * event, so the test can read `defaultPrevented` — that flag is exactly the
 * difference between "the browser would navigate" and "the panel opened".
 * `act` keeps React's re-render inside the dispatch, as a browser click does.
 */
function clickFooterLink(init: MouseEventInit = {}) {
  const anchor = screen.getByRole('link', { name: 'onze nerds' })
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...init })
  act(() => {
    anchor.dispatchEvent(event)
  })
  return event
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('WidgetChatSurface footer link panel', () => {
  it('opens a plain-clicked footer link in an in-page panel instead of navigating', () => {
    renderSurface({ footerLinksInWidget: true })

    const event = clickFooterLink()

    // Nothing was allowed to navigate; the panel took the click instead.
    expect(event.defaultPrevented).toBe(true)

    const frame = document.querySelector('iframe')
    expect(frame).not.toBeNull()
    expect(frame?.getAttribute('src')).toBe('https://example.com/agenda')

    // The panel head names the link it is showing.
    const panel = screen.getByRole('region', { name: 'onze nerds' })
    expect(within(panel).getByText('onze nerds')).toBeTruthy()
  })

  it('offers the same URL as a new-tab link and gives a footer frame no browser rights', () => {
    renderSurface({ footerLinksInWidget: true })
    clickFooterLink()

    const panel = screen.getByRole('region', { name: 'onze nerds' })
    const newTabLink = within(panel).getByRole('link', { name: 'widget_chat_panel_open_new_tab' })
    expect(newTabLink.getAttribute('href')).toBe('https://example.com/agenda')
    expect(newTabLink.getAttribute('target')).toBe('_blank')
    expect(newTabLink.getAttribute('rel')).toBe('noopener noreferrer')

    expect(document.querySelector('iframe')?.getAttribute('allow')).toBe('')

    // The head also carries the control that returns to the chat.
    expect(within(panel).getByRole('button', { name: 'widget_chat_panel_back' })).toBeTruthy()
  })

  it('leaves footer links as plain new-tab anchors when the flag is off', () => {
    renderSurface()

    const event = clickFooterLink()

    expect(event.defaultPrevented).toBe(false)
    expect(document.querySelector('iframe')).toBeNull()
    const anchor = screen.getByRole('link', { name: 'onze nerds' })
    expect(anchor.getAttribute('target')).toBe('_blank')
    expect(anchor.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('does not capture a modifier click, which must still reach a new tab', () => {
    renderSurface({ footerLinksInWidget: true })

    const event = clickFooterLink({ metaKey: true })

    expect(event.defaultPrevented).toBe(false)
    expect(document.querySelector('iframe')).toBeNull()
  })
})

describe('WidgetChatSurface booking panel', () => {
  it('opens the booking module in the same panel, with the rights it needs', () => {
    renderSurface({
      footerText: null,
      nerdsEnabled: true,
      nerdsBookingUrl: 'https://support.voys.nl/agenda/workflow/voys',
    })

    const anchor = screen.getByRole('link', { name: 'onze nerds' })
    act(() => {
      anchor.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    })

    const frame = document.querySelector('iframe')
    expect(frame?.getAttribute('src')).toBe(
      'https://support.voys.nl/agenda/workflow/voys?embed=1&lng=nl',
    )
    // Only this known destination may use the booking module's capabilities.
    expect(frame?.getAttribute('allow')).toBe('clipboard-write; payment; geolocation')
    expect(screen.getByRole('region', { name: 'onze nerds' })).toBeTruthy()
  })

  it('moves focus into the panel and makes the chat behind it inert', () => {
    renderSurface({ footerLinksInWidget: true })
    const anchor = screen.getByRole('link', { name: 'onze nerds' })
    anchor.focus()

    clickFooterLink()

    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: 'widget_chat_panel_back' }),
    )
    expect(document.querySelector('[data-widget-footer]')?.closest('[inert]')).not.toBeNull()

    act(() => {
      screen.getByRole('button', { name: 'widget_chat_panel_back' }).click()
    })

    // Closing hands the conversation back, focus included: the composer is
    // where the visitor carries on.
    expect(document.activeElement).toBe(screen.getByRole('textbox'))
    expect(document.querySelector('[data-widget-footer]')?.closest('[inert]')).toBeNull()
  })
})

describe('WidgetChatSurface focus on first render', () => {
  it('leaves the visitor where the page put them until a panel has been open', () => {
    renderSurface({ footerLinksInWidget: true })

    // Opening the share page must not pull focus into the composer; only
    // closing a panel does that.
    expect(document.activeElement).toBe(document.body)
  })
})
