import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import type { WidgetDetailResponse, WidgetPreviewSessionResponse } from '../../-types'

const fetchSession = vi.fn()
const widget = {
  id: 'widget-uuid-1', widget_id: 'wgt_test', name: 'Internal name', description: null,
  public_share_enabled: false, allow_any_origin: false, rate_limit_rpm: 60,
  kb_access_count: 0, last_used_at: null, created_at: '2026-01-01', created_by: 'user-1', kb_access: [],
  widget_config: {
    title: 'Voys Help', welcome_message: 'Welkom', conversation_starters: [],
    css_variables: { '--custom': 'kept' }, primary_color: '#270697', theme: 'light',
    hide_disclaimer: false, ai_disclosure_override: 'Intro', footer_text: 'Footer',
    show_sources: true, show_meta: false, collect_user_info: false, page_context_enabled: true,
    allowed_origins: [], system_prompt: '', template_slug: null, widget_position: 'right',
  },
} satisfies WidgetDetailResponse
const session = {
  session_token: 'preview-token', chat_endpoint: '/partner/v1/chat/completions',
  session_expires_at: '2099-01-01T00:00:00Z', session_id: 'preview-conversation-1',
  tenant_css_variables: { '--tenant': 'kept' },
} satisfies WidgetPreviewSessionResponse

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: object) => ({ options, useParams: () => ({ id: widget.id }) }),
}))
vi.mock('@/features/widgets/chat/useHideGlobalWidget', () => ({ useHideGlobalWidget: vi.fn() }))
vi.mock('@/paraglide/messages', () => ({
  admin_shared_error_generic: () => 'error',
  admin_widgets_preview_badge: () => 'Preview',
  widget_chat_preview_session_error: () => 'preview error',
  widget_chat_widget_load_error: () => 'widget error',
}))
vi.mock('../../-hooks', () => ({
  useWidget: () => ({ data: widget, isPending: false }),
  useWidgetPreviewSession: () => ({ data: session, isPending: false }),
  widgetPreviewSessionQueryKey: (id: string) => ['admin-widget-preview-session', id],
  fetchWidgetPreviewSession: (id: string, sessionId?: string) => fetchSession(id, sessionId),
}))
vi.mock('../WidgetEmbedPreview', () => ({
  WidgetEmbedPreview: ({ config, fetchConfig }: { config: Record<string, unknown>; fetchConfig: (id?: string) => Promise<unknown> }) => (
    <button type="button" data-testid="embed-preview" data-config={JSON.stringify(config)} onClick={() => void fetchConfig('preview-conversation-1')} />
  ),
}))

import { Route } from '../../$id_.test'

describe('admin widget test route', () => {
  it('uses the regular embed renderer with saved styling and preview-only renewal', async () => {
    fetchSession.mockResolvedValue(session)
    const Page = Route.options.component!
    render(<QueryClientProvider client={new QueryClient()}><Page /></QueryClientProvider>)
    const preview = await screen.findByTestId('embed-preview')
    const config = JSON.parse(preview.dataset.config!)
    expect(config).toMatchObject({
      footer_text: 'Footer', css_variables: { '--custom': 'kept' },
      tenant_css_variables: { '--tenant': 'kept' }, page_context_enabled: false,
      session_id: 'preview-conversation-1',
    })
    fireEvent.click(preview)
    await waitFor(() => expect(fetchSession).toHaveBeenCalledWith(widget.id, 'preview-conversation-1'))
  })
})
