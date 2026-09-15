/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §3 (fase 1c) — the admin widget Activity tab
 * keeps the channel stats and hands conversation review to the knowledge side.
 *
 * Two contracts: the tab links to the knowledge conversation queue for THIS
 * widget, and it no longer renders its own conversation list (which came with
 * a drawer, a pattern the portal UI standards forbid for admin entity work).
 */
import { type ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as m from '@/paraglide/messages'
import { ActivityTab } from '../ActivityTab'
import type { WidgetDetailResponse } from '../../../-types'

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    // Serialise the target so the search params are assertable as a URL.
    Link: ({
      to,
      search,
      children,
    }: {
      to: string
      search?: Record<string, unknown>
      children?: ReactNode
    }) => (
      <a
        href={`${to}?${new URLSearchParams(
          Object.fromEntries(Object.entries(search ?? {}).map(([k, v]) => [k, String(v)])),
        ).toString()}`}
      >
        {children}
      </a>
    ),
  }
})

const apiFetchMock = vi.fn()
let unlockedFeatures: string[] = ['widgets', 'knowledge_activity']
vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

// fetchMe goes through raw fetch, not apiFetch, so the tenant unlocks the
// link depends on are mocked at the module boundary.
vi.mock('@/lib/api-me', () => ({
  fetchMe: () =>
    Promise.resolve({
      portal_role: 'admin',
      roles: [],
      capabilities: [],
      platform_unlocked_features: unlockedFeatures,
    }),
}))

const widget = {
  id: 'wid-1',
  name: 'Support widget',
} as WidgetDetailResponse

const stats = {
  period: '7d',
  total_conversations: 4,
  total_messages: 9,
  avg_messages_per_conversation: 2.25,
  top_queries: [{ query: 'hoe lang is de levertijd', count: 3 }],
  hourly_activity: Array(24).fill(1),
  outcome_counts: {
    resolved: 2,
    escalated: 1,
    abandoned: 0,
    unknown: 0,
    unlabeled: 1,
  },
}

beforeEach(() => {
  unlockedFeatures = ['widgets', 'knowledge_activity']
  apiFetchMock.mockReset()
  apiFetchMock.mockImplementation((path: string) => {
    if (path.includes('/stats')) return stats
    if (path.includes('/conversations')) return []
    return {}
  })
})

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ActivityTab widget={widget} />
    </QueryClientProvider>,
  )
}

describe('admin widget ActivityTab — conversations move to the knowledge side', () => {
  it('links to the knowledge conversation queue prefiltered to this widget', async () => {
    renderTab()
    const link = await screen.findByRole('link', {
      name: m.admin_widgets_activity_review_conversations_link(),
    })

    expect(link.getAttribute('href')).toContain('/app/knowledge/activity')
    expect(link.getAttribute('href')).toContain('widget_id=wid-1')
  })

  it('keeps the stats and drops its own recent-conversations list', async () => {
    renderTab()
    // Stats survived: the top-questions list is still there.
    expect(await screen.findByText('hoe lang is de levertijd')).toBeTruthy()
    expect(
      screen.queryByText(m.admin_widgets_activity_recent_conversations_title()),
    ).toBeNull()
  })

  it('hides the link when the tenant lacks the knowledge_activity unlock', async () => {
    unlockedFeatures = ['widgets']
    renderTab()
    await screen.findByText(stats.top_queries[0].query)
    expect(
      screen.queryByRole('link', { name: m.admin_widgets_activity_review_conversations_link() }),
    ).toBeNull()
  })
})
