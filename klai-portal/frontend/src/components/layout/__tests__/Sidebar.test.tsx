/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §3 — the worklist counter the sidebar shows for
 * Gesprekken hangs on the *child* nav item, so a child badge has to render.
 * A badge that only renders on top-level items leaves the queue invisible.
 */
import type { ReactNode } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Library, MessageSquare } from 'lucide-react'

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  )
  return {
    ...actual,
    useLocation: () => ({ pathname: '/app/knowledge/activity' }),
    Link: ({ children }: { children?: ReactNode }) => <a>{children}</a>,
  }
})

vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: undefined }),
}))

vi.mock('@/paraglide/messages', () => ({
  sidebar_expand: () => 'Expand sidebar',
  sidebar_collapse: () => 'Collapse sidebar',
  sidebar_go_to_app: () => 'Go to the app',
  sidebar_go_to_admin: () => 'Go to admin',
}))

import { Sidebar } from '../Sidebar'

// The real shape `getAppNavItems` builds for a knowledge admin.
const NAV_ITEMS = [
  {
    to: '/app/knowledge',
    label: 'Kennisbanken',
    icon: Library,
    children: [
      { to: '/app/knowledge', label: 'Kennisbanken', icon: Library, end: true },
      { to: '/app/knowledge/activity', label: 'Gesprekken', icon: MessageSquare, badgeCount: 3 },
    ],
  },
]

describe('Sidebar badges', () => {
  it('renders the badge count of a child nav item', () => {
    render(<Sidebar navItems={NAV_ITEMS} />)

    expect(screen.getByText('3')).toBeTruthy()
  })
})
