/**
 * Tests for the conversation-starters step of the widget creation wizard
 * (/admin/widgets/new). Same contract as the Appearance tab on the detail
 * page: three numbered, optional fields; save produces a list with blank
 * fields dropped and the filled fields kept in the order they were typed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// ---------------------------------------------------------------------------
// Module mocks - must be at the top level before any imports of the SUT.
// ---------------------------------------------------------------------------

const { MESSAGE_KEYS } = vi.hoisted(() => ({
  MESSAGE_KEYS: [
    'admin_shared_error_generic',
    'admin_shared_field_name',
    'admin_shared_wizard_cancel',
    'admin_shared_wizard_create',
    'admin_shared_wizard_error_name_too_short',
    'admin_shared_wizard_error_no_kb_selected',
    'admin_shared_wizard_next',
    'admin_shared_wizard_previous',
    'admin_shared_wizard_step_details',
    'admin_shared_wizard_step_kb_access',
    'admin_widgets_allow_any_origin_label',
    'admin_widgets_allow_any_origin_warning',
    'admin_widgets_appearance_section_brand',
    'admin_widgets_appearance_section_chat_display',
    'admin_widgets_appearance_section_position',
    'admin_widgets_appearance_section_starters',
    'admin_widgets_appearance_section_welcome',
    'admin_widgets_brand_color_help',
    'admin_widgets_brand_color_label',
    'admin_widgets_brand_color_placeholder',
    'admin_widgets_collect_user_info_help',
    'admin_widgets_collect_user_info_label',
    'admin_widgets_create',
    'admin_widgets_details_role_scope_help',
    'admin_widgets_details_role_scope_label',
    'admin_widgets_details_section_ai',
    'admin_widgets_details_section_basics',
    'admin_widgets_name_placeholder',
    'admin_widgets_page_context_help',
    'admin_widgets_page_context_label',
    'admin_widgets_position_left',
    'admin_widgets_position_right',
    'admin_widgets_role_scope_placeholder',
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
    'admin_widgets_widget_origins_label',
    'admin_widgets_widget_origins_placeholder',
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
    'admin_widgets_widget_welcome_placeholder',
    'admin_widgets_wizard_error_invalid_origins',
    'admin_widgets_wizard_kb_access_intro_widget',
    'admin_widgets_wizard_step_appearance',
    'admin_widgets_wizard_step_embed',
  ],
}))

vi.mock('@/paraglide/messages', () =>
  Object.fromEntries(MESSAGE_KEYS.map((key) => [key, () => key])),
)

const mutateMock = vi.fn()
vi.mock('../-hooks', () => ({
  useCreateWidget: () => ({ mutate: mutateMock, isPending: false, error: null }),
}))

vi.mock('../_components/KbAccessEditor', () => ({
  KbAccessEditor: ({ onChange }: { onChange: (ids: number[]) => void }) => (
    <button type="button" onClick={() => onChange([1])}>select-kb</button>
  ),
}))

const apiFetchMock = vi.fn().mockResolvedValue([])
vi.mock('@/lib/apiFetch', () => ({
  apiFetch: (url: string, init?: RequestInit) => apiFetchMock(url, init),
}))

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: object) => ({ options }),
  useNavigate: () => vi.fn(),
}))

// ---------------------------------------------------------------------------
// Import SUT after mocks are registered.
// ---------------------------------------------------------------------------

import { Route } from '../new'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function renderWizard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Page = Route.options.component!
  return render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  )
}

/** Walks the wizard from step 1 up to and including the appearance step. */
function goToAppearanceStep() {
  fireEvent.change(document.getElementById('widget-name')!, { target: { value: 'Help Bot' } })
  fireEvent.click(screen.getByText('admin_shared_wizard_next'))
  fireEvent.click(screen.getByText('select-kb'))
  fireEvent.click(screen.getByText('admin_shared_wizard_next'))
}

beforeEach(() => {
  mutateMock.mockReset()
  apiFetchMock.mockReset()
  apiFetchMock.mockResolvedValue([])
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('new widget wizard - conversation starters', () => {
  it('renders exactly 3 starter fields, no more and no less', () => {
    renderWizard()
    goToAppearanceStep()

    expect(document.getElementById('widget-starter-1')).not.toBeNull()
    expect(document.getElementById('widget-starter-2')).not.toBeNull()
    expect(document.getElementById('widget-starter-3')).not.toBeNull()
    expect(document.getElementById('widget-starter-4')).toBeNull()
  })

  it('creates the widget with the filled starters as a list, blanks dropped, order kept', () => {
    renderWizard()
    goToAppearanceStep()

    fireEvent.change(document.getElementById('widget-starter-1')!, { target: { value: 'Eerste vraag' } })
    // Field 2 left blank on purpose.
    fireEvent.change(document.getElementById('widget-starter-3')!, { target: { value: '  Derde vraag  ' } })

    // Advance to the last (embed) step and submit.
    fireEvent.click(screen.getByText('admin_shared_wizard_next'))
    fireEvent.click(screen.getByText('admin_shared_wizard_create'))

    expect(mutateMock).toHaveBeenCalledTimes(1)
    const [payload] = mutateMock.mock.calls[0] as [{ widget_config: { conversation_starters: string[] } }]
    expect(payload.widget_config.conversation_starters).toEqual(['Eerste vraag', 'Derde vraag'])
  })

  it('creates the widget with no starters when every field is left blank', () => {
    renderWizard()
    goToAppearanceStep()

    fireEvent.click(screen.getByText('admin_shared_wizard_next'))
    fireEvent.click(screen.getByText('admin_shared_wizard_create'))

    expect(mutateMock).toHaveBeenCalledTimes(1)
    const [payload] = mutateMock.mock.calls[0] as [{ widget_config: { conversation_starters: string[] } }]
    expect(payload.widget_config.conversation_starters).toEqual([])
  })
})
