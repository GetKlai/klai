import type { JSX, ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const navigate = vi.fn()

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  createFileRoute: () => (cfg: unknown) => ({
    ...(cfg as object),
    useSearch: () => ({ tab: undefined }),
  }),
}))

const apiFetchMock = vi.fn()

vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

vi.mock('@/paraglide/messages', () => {
  const messages = {
    admin_settings_heading: () => 'Settings',
    admin_settings_subtitle: () => 'Organisation name and account details.',
    admin_settings_tab_general: () => 'General',
    admin_settings_tab_security: () => 'Access & security',
    admin_settings_tab_privacy: () => 'Privacy',
    admin_settings_tab_features: () => 'Features',
    admin_settings_org_title: () => 'Organisation',
    admin_settings_org_description: () => 'The organisation identity used across the workspace.',
    admin_settings_org_name_label: () => 'Name',
    admin_settings_org_domain_label: () => 'Primary domain',
    admin_settings_org_domain_empty: () => 'No primary domain',
    admin_settings_language_title: () => 'Default language',
    admin_settings_language_description: () => 'The language used for invitation and verification emails sent to new users.',
    admin_settings_language_label: () => 'Default language for new users',
    admin_settings_language_nl: () => 'Dutch',
    admin_settings_language_en: () => 'English',
    admin_settings_save: () => 'Save',
    admin_settings_saving: () => 'Saving...',
    admin_settings_saved: () => 'Saved',
    admin_settings_error_fetch: () => 'Could not fetch settings',
    admin_settings_error_save: () => 'Save failed',
    admin_users_loading: () => 'Loading...',
  }
  return messages
})

import { Route as RouteCfg } from '../settings'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  navigate.mockReset()
  apiFetchMock.mockReset()
})

describe('AdminSettings page', () => {
  it('groups settings into tabs and replaces the placeholder with organisation details', async () => {
    apiFetchMock.mockResolvedValue({
      name: 'Klai',
      default_language: 'nl',
      mfa_policy: 'optional',
      auto_accept_same_domain: false,
      primary_domain: 'getklai.com',
      telemetry_level: 'shadow',
    })

    const Cfg = RouteCfg as unknown as { component: () => JSX.Element }
    render(
      <Wrapper>
        <Cfg.component />
      </Wrapper>,
    )

    expect(screen.getByRole('tab', { name: 'General' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Access & security' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Privacy' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Features' })).toBeTruthy()

    await waitFor(() => {
      expect(screen.getByText('getklai.com')).toBeTruthy()
    })

    expect(screen.queryByText('Placeholder')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Access & security' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/admin/settings',
      search: { tab: 'security' },
    })
  })
})
