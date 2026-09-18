/**
 * Tests for the "open the footer link inside the chat" switch (widget_config
 * key `footer_links_in_widget`).
 *
 * The switch only makes sense while the footer actually contains a link, so
 * it must appear exactly when the footer text carries an http(s) URL, mirror
 * the saved value, and follow edits to the footer textarea before save.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
    'admin_widgets_widget_starter_label',
    'admin_widgets_widget_starter_placeholder_1',
    'admin_widgets_widget_starter_placeholder_2',
    'admin_widgets_widget_starter_placeholder_3',
    'admin_widgets_widget_starters_help',
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
    'widget_chat_close',
    'widget_chat_copied',
    'widget_chat_default_empty_state',
    'widget_chat_input_placeholder',
    'widget_chat_meta_sources_many',
    'widget_chat_meta_sources_one',
    'widget_chat_new_conversation',
    'widget_chat_preview_session_error',
    'widget_chat_send',
    'widget_chat_share_link',
    'widget_chat_sources_label',
    'widget_chat_status_online',
    'widget_chat_typing',
    'widget_chat_user_info_email',
    'widget_chat_user_info_help',
    'widget_chat_user_info_name',
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

const FOOTER_WITH_LINK = 'Plan een afspraak met [onze nerds](https://voorbeeld.nl).'
const FOOTER_WITHOUT_LINK = 'AI-antwoorden kunnen fouten bevatten.'

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

function linkToggle(): HTMLInputElement | null {
  return document.getElementById('footer-links-in-widget') as HTMLInputElement | null
}

beforeEach(() => {
  apiFetchMock.mockReset()
  apiFetchMock.mockResolvedValue([])
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AppearanceTab - footer link target', () => {
  it('offers no in-chat link switch while the footer holds no link', () => {
    renderTab(makeWidget({ footer_text: FOOTER_WITHOUT_LINK }))

    expect(linkToggle()).toBeNull()
    expect(screen.queryByText('admin_widgets_footer_links_in_widget_label')).toBeNull()
  })

  it('shows the switch checked when a footer link is saved with the setting on', () => {
    renderTab(makeWidget({
      footer_text: FOOTER_WITHOUT_LINK + ' ' + FOOTER_WITH_LINK,
      footer_links_in_widget: true,
    }))

    const toggle = linkToggle()
    expect(toggle).not.toBeNull()
    expect(toggle!.checked).toBe(true)
    expect(screen.getByText('admin_widgets_footer_links_in_widget_label')).toBeTruthy()
    expect(screen.getByText('admin_widgets_footer_links_in_widget_help')).toBeTruthy()
  })

  it('reveals the switch once a link is typed into an empty footer', () => {
    renderTab(makeWidget({ footer_text: '' }))
    expect(linkToggle()).toBeNull()

    fireEvent.change(document.getElementById('widget-footer-text')!, {
      target: { value: FOOTER_WITH_LINK },
    })

    const toggle = linkToggle()
    expect(toggle).not.toBeNull()
    expect(toggle!.checked).toBe(false)
  })
})
