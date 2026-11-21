/**
 * A website connector whose login Klai could not renew ends its sync as
 * AUTH_ERROR; the row then offers "Log in again", which opens the edit page
 * where the owner pastes fresh cookies.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ConnectorSummary } from '../-kb-types'

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ isAuthenticated: true }) }))
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: { isAdmin: false, hasCapability: () => false } }),
}))

import { ConnectorRow } from '../-connectors-row'

function renderRow(status: string, onEdit = vi.fn()) {
  const connector: ConnectorSummary = {
    id: 'connector-1',
    name: 'Handbook',
    connector_type: 'web_crawler',
    config: {},
    schedule: null,
    is_enabled: true,
    last_sync_status: status,
    last_sync_at: null,
    last_sync_documents_ok: null,
    allowed_assertion_modes: null,
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <table>
        <tbody>
          <ConnectorRow
            connector={connector}
            isOwner
            isSyncing={false}
            reconnecting={false}
            reconnectFailed={false}
            onSync={vi.fn()}
            onReconnect={vi.fn()}
            onEdit={onEdit}
            onDelete={vi.fn()}
            onInvestigate={vi.fn()}
          />
        </tbody>
      </table>
    </QueryClientProvider>,
  )
  return onEdit
}

describe('website connector with a lost login', () => {
  it('offers Log in again, which opens the edit page', () => {
    const onEdit = renderRow('auth_error')
    fireEvent.click(screen.getByRole('button', { name: 'Log in again' }))
    expect(onEdit).toHaveBeenCalledWith('connector-1')
  })

  it('does not offer it after an ordinary failure', () => {
    renderRow('failed')
    expect(screen.queryByRole('button', { name: 'Log in again' })).toBeNull()
  })
})
