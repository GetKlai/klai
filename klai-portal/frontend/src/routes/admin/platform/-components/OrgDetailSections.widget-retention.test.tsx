import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PlatformOrg } from '../-types'

const retentionMutate = vi.hoisted(() => vi.fn())

vi.mock('../-hooks', () => ({
  usePlatformWidgetRetention: () => ({
    isLoading: false,
    error: null,
    data: { days: 90, default_days: 7 },
  }),
  usePlatformUpdateWidgetRetention: () => ({
    mutate: retentionMutate,
    isPending: false,
    error: null,
  }),
}))

vi.mock('@/paraglide/messages', () => {
  const fixed = (value: string) => () => value
  return {
    widget_retention_title: fixed('Bewaartermijn widget-gesprekken (dagen)'),
    widget_retention_description: fixed('description'),
    widget_retention_label: fixed('Dagen'),
    widget_retention_default_hint: ({ days }: { days: number }) => `Standaard: ${days} dagen`,
    widget_retention_reset: fixed('Terug naar standaard'),
    widget_retention_error_invalid: fixed('invalid'),
    admin_settings_save: fixed('Opslaan'),
    admin_settings_saving: fixed('Opslaan...'),
    admin_settings_saved: fixed('Opgeslagen'),
    admin_settings_error_save: fixed('Opslaan mislukt'),
    admin_settings_error_fetch: fixed('Kon instellingen niet ophalen'),
    admin_users_loading: fixed('Laden...'),
  }
})

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import { WidgetRetentionSection } from './OrgDetailSections'

function makeOrg(overrides: Partial<PlatformOrg> = {}): PlatformOrg {
  return {
    id: 202,
    name: 'Voys',
    slug: 'voys',
    plan: 'knowledge',
    platform_unlocked_features: [],
    billing_status: 'active',
    billing_cycle: 'monthly',
    provisioning_status: 'ready',
    user_count: 1,
    bot_count: 1,
    kb_count: 1,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('WidgetRetentionSection', () => {
  beforeEach(() => {
    retentionMutate.mockReset()
  })

  it('renders the current override value', () => {
    render(<WidgetRetentionSection orgId="202" org={makeOrg()} />)

    const input = screen.getByLabelText<HTMLInputElement>('Dagen')
    expect(input.value).toBe('90')
    screen.getByText('Standaard: 7 dagen')
  })

  it('PATCHes {days: 90} on save', async () => {
    render(<WidgetRetentionSection orgId="202" org={makeOrg()} />)

    const input = screen.getByLabelText<HTMLInputElement>('Dagen')
    fireEvent.change(input, { target: { value: '90' } })
    fireEvent.click(screen.getByText('Opslaan'))

    await waitFor(() => {
      expect(retentionMutate).toHaveBeenCalledWith(90, expect.anything())
    })
  })
})
