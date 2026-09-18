/**
 * Tests for the "subjects this assistant does not answer" fields on the
 * widget Algemeen tab.
 *
 * The backend only honours the setting when customer-facing mode is on and
 * BOTH fields are filled, so the tab must not offer them for an internal
 * widget, and a save must carry both values through widget_config.
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
    'admin_shared_field_name',
    'admin_shared_save',
    'admin_shared_success_updated',
    'admin_widgets_details_role_scope_help',
    'admin_widgets_details_role_scope_label',
    'admin_widgets_details_section_ai',
    'admin_widgets_details_section_basics',
    'admin_widgets_name_placeholder',
    'admin_widgets_off_topic_reply_help',
    'admin_widgets_off_topic_reply_label',
    'admin_widgets_off_topic_reply_placeholder',
    'admin_widgets_off_topic_subjects_help',
    'admin_widgets_off_topic_subjects_label',
    'admin_widgets_off_topic_subjects_placeholder',
    'admin_widgets_page_context_help',
    'admin_widgets_page_context_label',
    'admin_widgets_role_scope_placeholder',
    'admin_widgets_support_mode_help',
    'admin_widgets_support_mode_label',
    'admin_widgets_tone_register_expressive',
    'admin_widgets_tone_register_expressive_help',
    'admin_widgets_tone_register_help',
    'admin_widgets_tone_register_label',
    'admin_widgets_tone_register_restrained',
    'admin_widgets_tone_register_restrained_help',
    'admin_widgets_widget_system_prompt_help',
    'admin_widgets_widget_system_prompt_label',
    'admin_widgets_widget_system_prompt_placeholder',
    'admin_widgets_widget_template_help',
    'admin_widgets_widget_template_label',
    'admin_widgets_widget_template_none',
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

import { DetailsTab } from '../DetailsTab'
import { WidgetPreviewProvider } from '../../../-preview'
import type { WidgetConfig, WidgetDetailResponse } from '../../../-types'

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
      support_mode: true,
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
        <DetailsTab widget={widget} />
      </WidgetPreviewProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  apiFetchMock.mockReset()
  apiFetchMock.mockResolvedValue([])
})

describe('DetailsTab - subjects the widget does not answer', () => {
  it('hides both fields while customer-facing mode is off, because the backend ignores them there', () => {
    renderTab(makeWidget({ support_mode: false }))

    expect(document.getElementById('widget-off-topic-subjects')).toBeNull()
    expect(document.getElementById('widget-off-topic-reply')).toBeNull()
  })

  it('loads the saved subjects and reply into the fields', () => {
    renderTab(
      makeWidget({
        off_topic_subjects: 'prijzen, offertes',
        off_topic_reply: 'Neem hiervoor contact met ons op.',
      }),
    )

    expect((document.getElementById('widget-off-topic-subjects') as HTMLTextAreaElement).value).toBe('prijzen, offertes')
    expect((document.getElementById('widget-off-topic-reply') as HTMLTextAreaElement).value).toBe(
      'Neem hiervoor contact met ons op.',
    )
  })

  it('saves both fields trimmed, so a half-filled setting cannot reach the backend unnoticed', async () => {
    renderTab(makeWidget())

    fireEvent.change(document.getElementById('widget-off-topic-subjects')!, {
      target: { value: '  prijzen, tarieven, offertes  ' },
    })
    fireEvent.change(document.getElementById('widget-off-topic-reply')!, {
      target: { value: 'Plan hiervoor een afspraak.' },
    })
    fireEvent.click(screen.getByText('admin_shared_save'))

    await waitFor(() => {
      const request = apiFetchMock.mock.calls.find(([url]) => url === '/api/admin/widgets/widget-uuid-1')
      const config = JSON.parse(String(request?.[1]?.body)).widget_config
      expect(config.off_topic_subjects).toBe('prijzen, tarieven, offertes')
      expect(config.off_topic_reply).toBe('Plan hiervoor een afspraak.')
    })
  })
})
