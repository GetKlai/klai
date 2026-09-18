/**
 * Tests for the conversation-starters fields on the widget Appearance tab.
 *
 * Three numbered, optional input fields replaced the old single "one per
 * line" textarea (max was 6, now 3). Saving must produce a list with blank
 * fields dropped and the filled fields kept in the order they were typed.
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

const { MESSAGE_KEYS } = vi.hoisted(() => ({
  MESSAGE_KEYS: [
    'admin_shared_error_generic',
    'admin_shared_save',
    'admin_shared_success_updated',
    'admin_widgets_appearance_section_brand',
    'admin_widgets_appearance_section_chat_display',
    'admin_widgets_appearance_section_position',
    'admin_widgets_appearance_section_starters',
    'admin_widgets_appearance_section_welcome',
    'admin_widgets_ai_disclosure_override_help',
    'admin_widgets_ai_disclosure_override_label',
    'admin_widgets_ai_disclosure_default',
    'admin_widgets_footer_links_in_widget_help',
    'admin_widgets_footer_links_in_widget_label',
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
    'admin_widgets_page_context_help',
    'admin_widgets_page_context_label',
    'admin_widgets_position_left',
    'admin_widgets_position_right',
    'admin_widgets_show_meta_help',
    'admin_widgets_show_meta_label',
    'admin_widgets_show_sources_help',
    'admin_widgets_show_sources_label',
    'admin_widgets_theme_dark',
    'admin_widgets_theme_label',
    'admin_widgets_theme_light',
    'admin_widgets_welcome_help',
    'admin_widgets_welcome_label',
    'admin_widgets_widget_hide_disclaimer_help',
    'admin_widgets_widget_hide_disclaimer_label',
    'admin_widgets_widget_starter_label',
    'admin_widgets_widget_starter_placeholder_1',
    'admin_widgets_widget_starter_placeholder_2',
    'admin_widgets_widget_starter_placeholder_3',
    'admin_widgets_widget_starters_help',
    'admin_widgets_widget_title_help',
    'admin_widgets_widget_title_label',
    'admin_widgets_widget_welcome_placeholder',
    'widget_ai_disclaimer',
    'widget_style_advanced_title',
    'widget_style_border_radius',
    'widget_style_content_padding',
    'widget_style_font_bundled',
    'widget_style_font_family',
    'widget_style_font_help',
    'widget_style_font_inherit',
    'widget_style_font_system',
    'widget_style_header_background',
    'widget_style_header_control_background',
    'widget_style_header_text',
    'widget_style_input_font_size',
    'widget_style_line_height',
    'widget_style_message_font_size',
    'widget_style_message_gap',
    'widget_style_starter_font_size',
    'widget_style_window_height',
    'widget_style_window_width',
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

// ---------------------------------------------------------------------------
// Import SUT after mocks are registered.
// ---------------------------------------------------------------------------

import { AppearanceTab } from '../tabs/AppearanceTab'
import { WidgetPreviewProvider } from '../../-preview'
import type { WidgetConfig, WidgetDetailResponse } from '../../-types'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeWidget(configOverrides?: Partial<WidgetConfig>): WidgetDetailResponse {
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
      ...configOverrides,
    },
    kb_access: [],
  }
}

function renderTab(widget: WidgetDetailResponse) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <WidgetPreviewProvider widget={widget}>
        <AppearanceTab widget={widget} />
      </WidgetPreviewProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  apiFetchMock.mockReset()
  apiFetchMock.mockResolvedValue([])
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AppearanceTab - conversation starters', () => {
  it('renders exactly 3 starter fields, no more and no less', () => {
    renderTab(makeWidget())

    expect(document.getElementById('widget-starter-1')).not.toBeNull()
    expect(document.getElementById('widget-starter-2')).not.toBeNull()
    expect(document.getElementById('widget-starter-3')).not.toBeNull()
    expect(document.getElementById('widget-starter-4')).toBeNull()
  })

  it('loads fewer than 3 saved starters into the first fields, leaving the rest blank', () => {
    renderTab(makeWidget({ conversation_starters: ['Wat zijn de kosten?'] }))

    expect((document.getElementById('widget-starter-1') as HTMLInputElement).value).toBe('Wat zijn de kosten?')
    expect((document.getElementById('widget-starter-2') as HTMLInputElement).value).toBe('')
    expect((document.getElementById('widget-starter-3') as HTMLInputElement).value).toBe('')
  })

  it('saves the filled fields as a list, drops blanks, and keeps entry order', async () => {
    renderTab(makeWidget())

    fireEvent.change(document.getElementById('widget-starter-1')!, { target: { value: 'Eerste vraag' } })
    // Field 2 left blank on purpose.
    fireEvent.change(document.getElementById('widget-starter-3')!, { target: { value: '  Derde vraag  ' } })
    fireEvent.click(screen.getByText('admin_shared_save'))

    await waitFor(() => {
      const request = apiFetchMock.mock.calls.find(([url]) => url === '/api/admin/widgets/widget-uuid-1')
      const config = JSON.parse(String(request?.[1]?.body)).widget_config
      expect(config.conversation_starters).toEqual(['Eerste vraag', 'Derde vraag'])
    })
  })

  it('saves an empty list when every field is left blank', async () => {
    renderTab(makeWidget({ conversation_starters: ['Oude vraag'] }))

    fireEvent.change(document.getElementById('widget-starter-1')!, { target: { value: '' } })
    fireEvent.click(screen.getByText('admin_shared_save'))

    await waitFor(() => {
      const request = apiFetchMock.mock.calls.find(([url]) => url === '/api/admin/widgets/widget-uuid-1')
      const config = JSON.parse(String(request?.[1]?.body)).widget_config
      expect(config.conversation_starters).toEqual([])
    })
  })
})
