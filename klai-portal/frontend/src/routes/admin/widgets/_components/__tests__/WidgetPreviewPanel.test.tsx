/**
 * Tests for SPEC-WIDGET-PREVIEW-001: the admin preview panel.
 *
 * The panel must follow the NOT-YET-SAVED form state (name, welcome text),
 * flag that answer behaviour (instructions, customer mode) only follows the
 * SAVED settings, and degrade to a clean message when the preview session
 * cannot be started while the rest of the screen keeps working.
 *
 * The chat surface itself is replaced with a props-recording stub; sending
 * messages is the surface's own contract, not the panel's.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// ---------------------------------------------------------------------------
// Module mocks - must be at the top level before any imports of the SUT.
// ---------------------------------------------------------------------------

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

// Paraglide compiles to src/paraglide, which is generated rather than
// committed; resolve every message key the component tree uses to its own
// name for stable assertions.
const { MESSAGE_KEYS } = vi.hoisted(() => ({
  MESSAGE_KEYS: [
    'admin_shared_error_generic',
    'admin_shared_field_name',
    'admin_shared_save',
    'admin_shared_success_updated',
    'admin_widgets_appearance_section_brand',
    'admin_widgets_appearance_section_chat_display',
    'admin_widgets_appearance_section_position',
    'admin_widgets_appearance_section_starters',
    'admin_widgets_appearance_section_welcome',
    'admin_widgets_ai_disclosure_override_help',
    'admin_widgets_ai_disclosure_override_label',
    'admin_widgets_ai_disclosure_override_placeholder',
    'admin_widgets_ai_disclosure_default',
    'admin_widgets_footer_text_help',
    'admin_widgets_footer_text_label',
    'admin_widgets_footer_text_placeholder',
    'admin_widgets_brand_color_help',
    'admin_widgets_brand_color_label',
    'admin_widgets_brand_color_placeholder',
    'admin_widgets_background_color_help',
    'admin_widgets_background_color_label',
    'admin_widgets_background_color_placeholder',
    'admin_widgets_collect_user_info_help',
    'admin_widgets_collect_user_info_label',
    'admin_widgets_details_role_scope_help',
    'admin_widgets_details_role_scope_label',
    'admin_widgets_details_section_ai',
    'admin_widgets_details_section_basics',
    'admin_widgets_name_placeholder',
    'admin_widgets_page_context_help',
    'admin_widgets_page_context_label',
    'admin_widgets_position_left',
    'admin_widgets_position_right',
    'admin_widgets_preview_badge',
    'admin_widgets_preview_close',
    'admin_widgets_preview_loading',
    'admin_widgets_preview_model_note',
    'admin_widgets_preview_not_counted',
    'admin_widgets_preview_restart',
    'admin_widgets_preview_retry',
    'admin_widgets_preview_subtitle',
    'admin_widgets_role_scope_placeholder',
    'admin_widgets_show_meta_help',
    'admin_widgets_show_meta_label',
    'admin_widgets_show_sources_help',
    'admin_widgets_show_sources_label',
    'admin_widgets_support_mode_help',
    'admin_widgets_support_mode_label',
    'admin_widgets_theme_dark',
    'admin_widgets_theme_label',
    'admin_widgets_theme_light',
    'admin_widgets_welcome_help',
    'admin_widgets_welcome_label',
    'admin_widgets_widget_hide_disclaimer_help',
    'admin_widgets_widget_hide_disclaimer_label',
    'admin_widgets_widget_starters_help',
    'admin_widgets_widget_starters_label',
    'admin_widgets_widget_starters_placeholder',
    'admin_widgets_widget_system_prompt_help',
    'admin_widgets_widget_system_prompt_label',
    'admin_widgets_widget_system_prompt_placeholder',
    'admin_widgets_widget_template_help',
    'admin_widgets_widget_template_label',
    'admin_widgets_widget_template_none',
    'admin_widgets_widget_title_help',
    'admin_widgets_widget_title_label',
    'admin_widgets_widget_welcome_placeholder',
    'widget_ai_disclaimer',
    'widget_chat_preview_session_error',
  ],
}))

vi.mock('@/paraglide/messages', () =>
  Object.fromEntries(MESSAGE_KEYS.map((key) => [key, () => key])),
)

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    user: { profile: { sub: 'test-sub' } },
  }),
}))

const apiFetchMock = vi.fn()
vi.mock('@/lib/apiFetch', () => ({
  apiFetch: (url: string, init?: RequestInit) => apiFetchMock(url, init),
}))

// Props-recording stub: the panel hands the preview values to the surface,
// the tests read them back off the DOM.
interface StubProps {
  botName?: string
  headerTitle?: string
  primaryColor?: string
  backgroundColor?: string
  welcomeMessage?: string
  aiDisclosureOverride?: string
  footerText?: string | null
  hideDisclaimer?: boolean
}
vi.mock('@/features/widgets/chat/WidgetChatSurface', () => ({
  WidgetChatSurface: ({
    botName = '',
    headerTitle = '',
    primaryColor = '',
    backgroundColor = '',
    welcomeMessage = '',
    aiDisclosureOverride = '',
    footerText = '',
    hideDisclaimer = false,
  }: StubProps) => (
    <div
      data-testid="chat-surface"
      data-bot-name={botName}
      data-header-title={headerTitle}
      data-primary-color={primaryColor}
      data-background-color={backgroundColor}
      data-ai-disclosure={aiDisclosureOverride}
      data-footer-text={footerText ?? ''}
      data-hide-disclaimer={String(hideDisclaimer)}
    >
      {welcomeMessage}
    </div>
  ),
}))

// ---------------------------------------------------------------------------
// Import SUT after mocks are registered.
// ---------------------------------------------------------------------------

import { WidgetPreviewPanel } from '../WidgetPreviewPanel'
import { WidgetPreviewProvider } from '../../-preview'
import { DetailsTab } from '../tabs/DetailsTab'
import { AppearanceTab } from '../tabs/AppearanceTab'
import type { WidgetDetailResponse } from '../../-types'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeWidget(overrides?: Partial<WidgetDetailResponse>): WidgetDetailResponse {
  return {
    id: 'widget-uuid-1',
    name: 'Test Widget',
    widget_id: 'wgt_test',
    description: null,
    allow_any_origin: false,
    public_share_enabled: false,
    rate_limit_rpm: 60,
    kb_access_count: 0,
    last_used_at: null,
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'user-1',
    widget_config: {
      allowed_origins: [],
      title: 'Test Widget',
      welcome_message: 'Welkom',
      system_prompt: '',
      css_variables: {},
      conversation_starters: [],
      hide_disclaimer: false,
      template_slug: null,
      primary_color: '#fcaa2d',
      theme: 'light',
      show_sources: true,
      show_meta: false,
      collect_user_info: false,
      page_context_enabled: false,
      widget_position: 'right',
    },
    kb_access: [],
    ...overrides,
  }
}

function renderScreen(widget: WidgetDetailResponse, tab: 'details' | 'appearance') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <WidgetPreviewProvider widget={widget}>
        <div>
          {tab === 'details' ? <DetailsTab widget={widget} /> : <AppearanceTab widget={widget} />}
          <WidgetPreviewPanel widget={widget} onCollapse={() => {}} />
        </div>
      </WidgetPreviewProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  apiFetchMock.mockReset()
  apiFetchMock.mockImplementation((url: string) => {
    if (url.includes('/preview-session')) {
      return Promise.resolve({
        session_token: 'preview-token',
        chat_endpoint: '/partner/v1/chat/completions',
        session_expires_at: '2026-01-01T02:00:00Z',
      })
    }
    return Promise.resolve([])
  })
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('WidgetPreviewPanel - SPEC-WIDGET-PREVIEW-001', () => {
  it('follows the unsaved widget name from the details form', async () => {
    const widget = makeWidget()
    renderScreen(widget, 'details')

    await screen.findByTestId('chat-surface')
    expect(screen.getByTestId('chat-surface').dataset.botName).toBe('Test Widget')

    const nameInput = document.getElementById('widget-name') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'Servicebot' } })

    expect(screen.getByTestId('chat-surface').dataset.botName).toBe('Servicebot')
  })

  it('follows the unsaved welcome text and colour from the appearance form', async () => {
    const widget = makeWidget()
    renderScreen(widget, 'appearance')

    const surface = await screen.findByTestId('chat-surface')
    expect(surface.textContent).toBe('Welkom')

    const welcomeInput = document.getElementById('widget-welcome') as HTMLInputElement
    fireEvent.change(welcomeInput, { target: { value: 'Hoi, kan ik helpen?' } })
    const colorInput = document.getElementById('widget-primary-color') as HTMLInputElement
    fireEvent.change(colorInput, { target: { value: '#2266ee' } })

    expect(screen.getByTestId('chat-surface').textContent).toBe('Hoi, kan ik helpen?')
    expect(screen.getByTestId('chat-surface').dataset.primaryColor).toBe('#2266ee')
  })

  it('previews saved and unsaved background, introduction, and footer settings', async () => {
    const widget = makeWidget()
    widget.name = 'Interne widgetnaam'
    widget.widget_config.title = 'Voys Help NL'
    widget.widget_config.css_variables = { '--klai-background-color': '#f0f0f0' }
    widget.widget_config.ai_disclosure_override = 'Bestaande introductie'
    widget.widget_config.footer_text = 'Bestaande footer'
    renderScreen(widget, 'appearance')

    const surface = await screen.findByTestId('chat-surface')
    expect(surface.dataset.botName).toBe('Interne widgetnaam')
    expect(surface.dataset.headerTitle).toBe('Voys Help NL')
    expect(surface.dataset.backgroundColor).toBe('#f0f0f0')
    expect(surface.dataset.aiDisclosure).toBe('Bestaande introductie')
    expect(surface.dataset.footerText).toBe('Bestaande footer')

    fireEvent.change(document.getElementById('widget-background-color')!, {
      target: { value: '#ffffff' },
    })
    fireEvent.change(document.getElementById('widget-ai-disclosure-override')!, {
      target: { value: 'Nieuwe introductie' },
    })
    fireEvent.change(document.getElementById('widget-footer-text')!, {
      target: { value: 'Nieuwe footer' },
    })

    expect(surface.dataset.backgroundColor).toBe('#ffffff')
    expect(surface.dataset.aiDisclosure).toBe('Nieuwe introductie')
    expect(surface.dataset.footerText).toBe('Nieuwe footer')
    expect(apiFetchMock.mock.calls.some(([url]) => url === '/api/admin/widgets/widget-uuid-1')).toBe(false)

    fireEvent.change(document.getElementById('widget-ai-disclosure-override')!, {
      target: { value: '' },
    })
    fireEvent.change(document.getElementById('widget-footer-text')!, {
      target: { value: '' },
    })
    expect(surface.dataset.aiDisclosure).toBe('')
    expect(surface.dataset.footerText).toBe('')
  })

  it('saves and repopulates the selected widget background colour', async () => {
    const widget = makeWidget()
    widget.widget_config.css_variables = { '--existing-variable': 'keep-me' }
    const rendered = renderScreen(widget, 'appearance')

    const backgroundInput = document.getElementById('widget-background-color') as HTMLInputElement
    expect(backgroundInput.value).toBe('#fffef2')
    fireEvent.click(screen.getByText('admin_widgets_theme_dark'))
    expect(backgroundInput.value).toBe('#191918')

    fireEvent.change(backgroundInput, { target: { value: '#123456' } })
    fireEvent.click(screen.getByText('admin_shared_save'))

    await waitFor(() => {
      const request = apiFetchMock.mock.calls.find(([url]) =>
        url === '/api/admin/widgets/widget-uuid-1',
      )
      const body = JSON.parse(String(request?.[1]?.body))
      expect(body.widget_config.css_variables).toEqual({
        '--existing-variable': 'keep-me',
        '--klai-background-color': '#123456',
      })
    })

    rendered.unmount()
    widget.widget_config.css_variables['--klai-background-color'] = '#123456'
    renderScreen(widget, 'appearance')
    expect((document.getElementById('widget-background-color') as HTMLInputElement).value).toBe('#123456')
  })

  it('preserves existing default text and booking links when only the background changes', async () => {
    const widget = makeWidget()
    widget.widget_config.ai_disclosure_override = null
    widget.widget_config.footer_text = null
    widget.widget_config.hide_disclaimer = true
    renderScreen(widget, 'appearance')
    fireEvent.change(document.getElementById('widget-background-color')!, {
      target: { value: '#ffffff' },
    })
    fireEvent.click(screen.getByText('admin_shared_save'))
    await waitFor(() => {
      const request = apiFetchMock.mock.calls.find(([url]) => url === '/api/admin/widgets/widget-uuid-1')
      const config = JSON.parse(String(request?.[1]?.body)).widget_config
      expect(config.ai_disclosure_override).toBeNull()
      expect(config.footer_text).toBeNull()
      expect(config.hide_disclaimer).toBe(true)
    })
  })

  it('saves a separate header title and explicit blank introduction and footer', async () => {
    const widget = makeWidget()
    widget.name = 'Interne widgetnaam'
    widget.widget_config.title = 'Oude titel'
    widget.widget_config.ai_disclosure_override = null
    widget.widget_config.footer_text = null
    widget.widget_config.hide_disclaimer = true
    renderScreen(widget, 'appearance')

    const title = document.getElementById('widget-header-title') as HTMLInputElement
    const intro = document.getElementById('widget-ai-disclosure-override') as HTMLTextAreaElement
    const footer = document.getElementById('widget-footer-text') as HTMLTextAreaElement
    expect(title.value).toBe('Oude titel')
    expect(intro.value).toBe('admin_widgets_ai_disclosure_default')
    expect(footer.value).toBe('widget_ai_disclaimer')
    expect(screen.queryByText('admin_widgets_widget_hide_disclaimer_label')).toBeNull()
    expect(intro.maxLength).toBe(500)
    expect(footer.maxLength).toBe(2000)
    const surface = await screen.findByTestId('chat-surface')

    fireEvent.change(title, { target: { value: 'Nieuwe titel' } })
    fireEvent.change(intro, { target: { value: '' } })
    fireEvent.change(footer, { target: { value: '' } })
    expect(surface.dataset.botName).toBe('Interne widgetnaam')
    expect(surface.dataset.headerTitle).toBe('Nieuwe titel')
    fireEvent.click(screen.getByText('admin_shared_save'))

    await waitFor(() => {
      const request = apiFetchMock.mock.calls.find(([url]) => url === '/api/admin/widgets/widget-uuid-1')
      const config = JSON.parse(String(request?.[1]?.body)).widget_config
      expect(config.title).toBe('Nieuwe titel')
      expect(config.ai_disclosure_override).toBe('')
      expect(config.footer_text).toBe('')
      expect(config.hide_disclaimer).toBe(false)
    })
  })

  it('flags that answer behaviour uses the saved settings while instructions are edited', async () => {
    renderScreen(makeWidget(), 'details')

    await screen.findByTestId('chat-surface')
    expect(
      screen.queryByText('admin_widgets_preview_model_note'),
    ).toBeNull()

    const prompt = document.getElementById('widget-system-prompt') as HTMLTextAreaElement
    fireEvent.change(prompt, { target: { value: 'Doe alsof je een ander bent.' } })

    expect(screen.getByText('admin_widgets_preview_model_note')).toBeTruthy()
  })

  it('shows a clean error and keeps the rest of the screen when the preview session fails', async () => {
    apiFetchMock.mockImplementation((url: string) => {
      if (url.includes('/preview-session')) {
        return Promise.reject(new Error('Widget auth not configured'))
      }
      return Promise.resolve([])
    })

    renderScreen(makeWidget(), 'details')

    await screen.findByText('widget_chat_preview_session_error')
    expect(screen.getByText('Widget auth not configured')).toBeTruthy()
    // The settings form beside the panel keeps working.
    expect(document.getElementById('widget-name')).toBeTruthy()
    // No chat surface is mounted.
    expect(screen.queryByTestId('chat-surface')).toBeNull()
  })

  it('states that preview conversations do not count toward statistics', async () => {
    renderScreen(makeWidget(), 'appearance')

    await screen.findByTestId('chat-surface')
    expect(screen.getByText('admin_widgets_preview_not_counted')).toBeTruthy()
  })
})
