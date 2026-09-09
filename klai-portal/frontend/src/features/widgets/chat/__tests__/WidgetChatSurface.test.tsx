/**
 * Tests for the nerds booking footer (Voys-specific integration).
 *
 * The public share link must never rewrite the client's fixed disclosure
 * sentence through the portal translation: with the integration on, the
 * footer shows the verbatim sentence and "onze nerds" links to the same
 * embed request the widget's iframe panel makes. Without the integration,
 * the footer stays exactly what it was — the portal-translated disclaimer.
 */
import type { ComponentProps } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// Paraglide compiles to src/paraglide, which is generated rather than
// committed; stub the runtime locale and resolve every message key the
// component uses to its own name for stable assertions.
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
      'widget_chat_user_info_email',
      'widget_chat_user_info_help',
      'widget_chat_user_info_name',
    ].map((key) => [key, () => key]),
  ),
)

import { WidgetChatSurface } from '../WidgetChatSurface'

const BOOKING_URL = 'https://support.voys.nl/book/voys?t=abc'

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

describe('WidgetChatSurface nerds footer', () => {
  it('keeps the portal disclaimer for widgets without the nerds integration', () => {
    renderSurface()
    // getBy* throws when absent: a plain render is the assertion.
    screen.getByText('widget_ai_disclaimer')
    expect(screen.queryByText(/onze nerds/)).toBeNull()
  })

  it('shows the fixed client sentence instead of the portal disclaimer when enabled', () => {
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: BOOKING_URL })
    screen.getByText(/baseert zich op zorgvuldig gekozen bronnen/)
    expect(screen.queryByText('widget_ai_disclaimer')).toBeNull()
  })

  it('links "onze nerds" to the same embed request the widget panel makes', () => {
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: BOOKING_URL })
    const link = screen.getByRole('link', { name: 'onze nerds' })
    expect(link.getAttribute('href')).toBe(`${BOOKING_URL}&embed=1&lng=nl`)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('starts the query string when the booking URL has none', () => {
    // The configured Voys URL is a bare path. Appending "&embed=1" to it makes
    // their router serve a 404 page instead of the booking flow, so the
    // separator has to be chosen rather than assumed. The fixture above
    // carries a query string and therefore never exercised this branch.
    const bare = 'https://support.voys.nl/agenda/abc/workflow/voys'
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: bare })
    expect(screen.getByRole('link', { name: 'onze nerds' }).getAttribute('href')).toBe(
      `${bare}?embed=1&lng=nl`,
    )
  })

  it('stays silent on the white-label toggle, same as the disclaimer it replaces', () => {
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: BOOKING_URL, hideDisclaimer: true })
    expect(screen.queryByText(/onze nerds/)).toBeNull()
    expect(screen.queryByText('widget_ai_disclaimer')).toBeNull()
  })

  it('does not build a footer sentence when the integration is on without a URL', () => {
    renderSurface({ nerdsEnabled: true, nerdsBookingUrl: '' })
    screen.getByText('widget_ai_disclaimer')
  })
})
