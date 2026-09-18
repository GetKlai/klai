/**
 * Tenant gate — connector row actions (knowledge_gaps rollout).
 *
 * A configured HubSpot support row stays visible for cleanup, but while the
 * tenant unlock is revoked its Sync and Edit actions (which would start new
 * collection/analysis, or open the blocked wizard) must disappear. Delete
 * stays available so the operator can clean up. Ordinary connector types are
 * never affected by the gate.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConnectorSummary } from '../-kb-types'

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ isAuthenticated: true }) }))

const capabilities: string[] = ['kb.gaps']
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    user: { isAdmin: false, hasCapability: (c: string) => capabilities.includes(c) },
  }),
}))

let unlockedFeatures: string[] = ['knowledge_gaps']
vi.mock('@/lib/api-me', () => ({
  fetchMe: () => Promise.resolve({ platform_unlocked_features: unlockedFeatures }),
}))

vi.mock('@/paraglide/messages', async () => {
  const actual = await vi.importActual<typeof import('@/paraglide/messages')>(
    '@/paraglide/messages',
  )
  return {
    ...actual,
    admin_connectors_action_sync: () => 'Sync',
    admin_connectors_action_edit: () => 'Edit',
    admin_connectors_action_delete: () => 'Delete',
  }
})

import { ConnectorRow } from '../-connectors-row'

function connector(overrides: Partial<ConnectorSummary> = {}): ConnectorSummary {
  return {
    id: 'connector-1',
    name: 'HubSpot inbox',
    connector_type: 'hubspot_support',
    config: {},
    schedule: null,
    is_enabled: true,
    last_sync_status: null,
    last_sync_at: null,
    last_sync_documents_ok: null,
    allowed_assertion_modes: null,
    ...overrides,
  }
}

function renderRow(c: ConnectorSummary) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <table>
        <tbody>
          <ConnectorRow
            connector={c}
            isOwner
            isSyncing={false}
            reconnecting={false}
            reconnectFailed={false}
            onSync={vi.fn()}
            onReconnect={vi.fn()}
            onEdit={vi.fn()}
            onDelete={vi.fn()}
            onInvestigate={vi.fn()}
          />
        </tbody>
      </table>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  capabilities.length = 0
  capabilities.push('kb.gaps')
  unlockedFeatures = ['knowledge_gaps']
})

describe('ConnectorRow HubSpot support gate', () => {
  it('shows Sync and Edit for a HubSpot row when the tenant is unlocked', async () => {
    renderRow(connector())
    expect(await screen.findByRole('button', { name: 'Sync' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy()
  })

  it('hides Sync and Edit but keeps Delete for a HubSpot row when revoked', async () => {
    unlockedFeatures = []
    renderRow(connector())
    expect(await screen.findByRole('button', { name: 'Delete' })).toBeTruthy()
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Sync' })).toBeNull(),
    )
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  it('leaves an ordinary connector row untouched while the tenant is locked', async () => {
    unlockedFeatures = []
    renderRow(connector({ connector_type: 'notion', name: 'Notion' }))
    expect(await screen.findByRole('button', { name: 'Sync' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy()
  })
})
