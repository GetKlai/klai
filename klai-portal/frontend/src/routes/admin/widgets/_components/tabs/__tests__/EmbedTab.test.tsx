/**
 * Tests for REQ-2 (Finding B-2): EmbedTab allow_any_origin toggle.
 *
 * SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2.
 *
 * AC-tested:
 * - When allow_any_origin=false: warning text is NOT rendered, origins
 *   textarea is enabled.
 * - When allow_any_origin=true (initial prop): warning text IS rendered.
 * - Clicking the checkbox toggles from false → true, showing the warning.
 *
 * @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

// ---------------------------------------------------------------------------
// Module mocks — must be at the top level before any imports of the SUT.
// ---------------------------------------------------------------------------

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/features/widgets/embed/snippet', () => ({
  buildWidgetEmbedSnippet: () => '<script src="..."></script>',
}))

vi.mock('@/features/widgets/config/origins', () => ({
  parseOrigins: (raw: string) => raw.split('\n').filter(Boolean),
  isValidOrigin: () => true,
}))

const updateMutateMock = vi.fn()
vi.mock('../../-hooks', () => ({
  useUpdateWidget: () => ({
    mutate: updateMutateMock,
    isPending: false,
    error: null,
  }),
}))

vi.mock('@/paraglide/messages', () => ({
  admin_widgets_allow_any_origin_label: () => 'Allow any origin',
  admin_widgets_allow_any_origin_warning: () =>
    'Conversations from any website will be attributed to this widget',
  admin_widgets_widget_origins_label: () => 'Allowed origins',
  admin_widgets_widget_origins_help: () => 'One origin per line',
  admin_widgets_widget_origins_placeholder: () => 'https://example.com',
  admin_widgets_share_link_title: () => 'Share link',
  admin_widgets_share_link_publish: () => 'Enable public share',
  admin_widgets_share_link_help: () => 'Share link help',
  admin_widgets_share_link_copy: () => 'Copy link',
  admin_widgets_share_link_copied: () => 'Copied!',
  admin_widgets_embed_code_title: () => 'Embed code',
  admin_widgets_embed_code_copy: () => 'Copy code',
  admin_widgets_embed_code_copied: () => 'Copied!',
  admin_widgets_save: () => 'Save',
  admin_widgets_test: () => 'Test',
  admin_shared_success_updated: () => 'Saved',
  admin_shared_error_generic: () => 'Something went wrong',
  admin_widgets_origins_error: () => 'Invalid origin',
  admin_widgets_widget_invalid_origins: () => 'Invalid origins',
  admin_widgets_widget_origins_empty_warning: () =>
    'No origins set — widget loads on any website',
  admin_shared_save: () => 'Save',
}))

// ---------------------------------------------------------------------------
// Import SUT after mocks are registered.
// ---------------------------------------------------------------------------

import { EmbedTab } from '../EmbedTab'
import type { WidgetDetailResponse } from '../../../-types'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function makeWidget(allow_any_origin = false): WidgetDetailResponse {
  return {
    id: 1,
    org_id: 1,
    name: 'Test Widget',
    widget_id: 'wgt_test',
    description: null,
    allow_any_origin,
    public_share_enabled: false,
    rate_limit_rpm: 60,
    last_used_at: null,
    created_at: '2026-01-01T00:00:00Z',
    created_by: 'user-1',
    widget_config: {
      allowed_origins: [],
      title: 'Test',
      welcome_message: '',
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
      widget_position: 'right',
    },
    kb_ids: [],
  }
}

beforeEach(() => {
  updateMutateMock.mockReset()
  // Stub clipboard and window.open so they don't throw in jsdom
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  })
  vi.stubGlobal('open', vi.fn())
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('EmbedTab — REQ-2 allow_any_origin toggle', () => {
  it('hides warning when allow_any_origin is false (default)', () => {
    render(
      <Wrapper>
        <EmbedTab widget={makeWidget(false)} />
      </Wrapper>,
    )

    // Warning text must NOT be present when toggle is off
    expect(
      screen.queryByText(
        'Conversations from any website will be attributed to this widget',
      ),
    ).toBeNull()

    // Origins textarea must be enabled (not disabled by the toggle)
    const textarea = document.getElementById(
      'widget-origins',
    ) as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    expect(textarea.disabled).toBe(false)
  })

  it('shows warning when allow_any_origin is true (initial prop)', () => {
    render(
      <Wrapper>
        <EmbedTab widget={makeWidget(true)} />
      </Wrapper>,
    )

    // Warning text MUST appear when toggle is on
    expect(
      screen.getByText(
        'Conversations from any website will be attributed to this widget',
      ),
    ).toBeTruthy()
  })

  it('clicking the checkbox transitions from false → true and shows warning', () => {
    render(
      <Wrapper>
        <EmbedTab widget={makeWidget(false)} />
      </Wrapper>,
    )

    // Warning absent before click
    expect(
      screen.queryByText(
        'Conversations from any website will be attributed to this widget',
      ),
    ).toBeNull()

    // Target the specific checkbox by id (there are two: public-share + allow-any-origin)
    const allowAnyOriginCheckbox = document.getElementById(
      'widget-allow-any-origin',
    ) as HTMLInputElement

    expect(allowAnyOriginCheckbox).toBeTruthy()
    expect(allowAnyOriginCheckbox.checked).toBe(false)

    fireEvent.click(allowAnyOriginCheckbox)

    // Warning MUST appear after click
    expect(
      screen.getByText(
        'Conversations from any website will be attributed to this widget',
      ),
    ).toBeTruthy()

    expect(allowAnyOriginCheckbox.checked).toBe(true)
  })
})
