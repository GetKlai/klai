import type { ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const apiFetchMock = vi.fn()
const fetchMeMock = vi.fn()

vi.mock('@/lib/apiFetch', async () => {
  const actual = await vi.importActual<typeof import('@/lib/apiFetch')>('@/lib/apiFetch')
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
})

vi.mock('@/lib/api-me', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api-me')>('@/lib/api-me')
  return { ...actual, fetchMe: (...args: unknown[]) => fetchMeMock(...args) }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}))

vi.mock('@/paraglide/messages', () => ({
  admin_settings_pii_title: () => 'Masking personal data for the AI model',
  admin_settings_pii_intro: () => 'This is an extra layer of data minimisation.',
  admin_settings_pii_group_contact_title: () => 'Contact details',
  admin_settings_pii_group_contact_description: () => 'Email addresses and phone numbers',
  admin_settings_pii_group_financial_title: () => 'Financial',
  admin_settings_pii_group_financial_description: () => 'IBANs and credit card numbers',
  admin_settings_pii_group_company_title: () => 'Company identifiers',
  admin_settings_pii_group_company_description: () => 'Chamber of commerce and VAT numbers',
  admin_settings_pii_group_location_title: () => 'Location',
  admin_settings_pii_group_location_description: () => 'Postcodes',
  admin_settings_pii_locked_title: () => 'Always on',
  admin_settings_pii_locked_description: () => 'Passwords, API keys and BSN',
  admin_settings_pii_locked_reason: () => 'Cannot be turned off.',
  admin_settings_pii_entity_email: () => 'Email address',
  admin_settings_pii_entity_phone: () => 'Phone number',
  admin_settings_pii_entity_iban: () => 'IBAN',
  admin_settings_pii_entity_creditcard: () => 'Credit card number',
  admin_settings_pii_entity_kvk: () => 'Chamber of commerce number',
  admin_settings_pii_entity_btw: () => 'VAT number',
  admin_settings_pii_entity_postcode: () => 'Postcode',
  admin_settings_pii_expand_details: () => 'View per item',
  admin_settings_pii_collapse_details: () => 'Hide per item',
  admin_settings_pii_mixed_hint: () => 'Mixed within this group',
  admin_settings_pii_readonly_hint: () => 'Only organisation admins can change this.',
  admin_settings_pii_limitations_title: () => 'What this does and does not catch',
  admin_settings_pii_limitation_names: () => 'Names are not detected.',
  admin_settings_pii_limitation_address: () => 'Addresses are recognised by postcode, not by street name.',
  admin_settings_pii_limitation_structured: () => 'Detection covers structured identifiers.',
  admin_settings_pii_limitation_context: () => 'The context around a masked value stays.',
  admin_settings_pii_limitation_false_positives: () => 'False positives happen.',
  admin_settings_pii_limitation_storage: () => 'This only governs what is sent to the AI model.',
  admin_settings_pii_limitation_locked_categories: () => 'Two categories cannot be switched off.',
  admin_settings_save: () => 'Save',
  admin_settings_saving: () => 'Saving...',
  admin_settings_saved: () => 'Saved',
  admin_settings_error_fetch: () => 'Could not fetch settings',
  admin_settings_error_save: () => 'Save failed',
  admin_users_loading: () => 'Loading...',
}))

import { PiiPolicySettingsSection } from '../_components/-PiiPolicySettingsSection'
import type { OrgSettings } from '../-settings-hooks'

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const baseSettings: OrgSettings = {
  name: 'Klai',
  default_language: 'nl',
  mfa_policy: 'optional',
  auto_accept_same_domain: false,
  primary_domain: 'getklai.com',
  telemetry_level: 'shadow',
  pii_masked_entities: [
    'EMAIL_ADDRESS',
    'PHONE_NUMBER',
    'IBAN_CODE',
    'CREDIT_CARD',
    'NL_KVK',
    'NL_BTW',
    'NL_POSTCODE',
  ],
}

function renderSection(settings: OrgSettings | undefined = baseSettings) {
  return render(
    <Wrapper>
      <PiiPolicySettingsSection settings={settings} isLoading={false} error={null} />
    </Wrapper>,
  )
}

beforeEach(() => {
  apiFetchMock.mockReset()
  fetchMeMock.mockReset()
  fetchMeMock.mockResolvedValue({ portal_role: 'admin', is_platform_admin: false })
})

describe('PiiPolicySettingsSection', () => {
  it('renders the four groups all-on and the locked always-on row', async () => {
    renderSection()

    await waitFor(() => {
      expect(screen.getByText('Contact details')).toBeTruthy()
    })
    expect(screen.getByText('Financial')).toBeTruthy()
    expect(screen.getByText('Company identifiers')).toBeTruthy()
    expect(screen.getByText('Location')).toBeTruthy()
    expect(screen.getByText('Always on')).toBeTruthy()
    expect(screen.getByText('Passwords, API keys and BSN')).toBeTruthy()

    const contactCheckbox = screen.getByRole('checkbox', { name: 'Contact details' })
    expect((contactCheckbox as HTMLInputElement).checked).toBe(true)
  })

  it('renders a mixed group as indeterminate with the mixed badge', async () => {
    renderSection({
      ...baseSettings,
      // Financial group disagrees: IBAN on, credit card off.
      pii_masked_entities: ['EMAIL_ADDRESS', 'PHONE_NUMBER', 'IBAN_CODE', 'NL_KVK', 'NL_BTW', 'NL_POSTCODE'],
    })

    await waitFor(() => {
      expect(screen.getByText('Mixed within this group')).toBeTruthy()
    })
    const financialCheckbox = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Financial' })
    expect(financialCheckbox.checked).toBe(false)
    expect(financialCheckbox.indeterminate).toBe(true)
  })

  it('expands the collapsed per-entity view to show individual toggles', async () => {
    renderSection({
      ...baseSettings,
      pii_masked_entities: ['EMAIL_ADDRESS', 'PHONE_NUMBER', 'IBAN_CODE', 'NL_KVK', 'NL_BTW', 'NL_POSTCODE'],
    })

    await waitFor(() => expect(screen.getByText('Financial')).toBeTruthy())
    // Contact, financial and company groups each have >1 entity and get an
    // expand button; financial is the second one in group order.
    fireEvent.click(screen.getAllByRole('button', { name: 'View per item' })[1])

    const ibanCheckbox = screen.getByRole<HTMLInputElement>('checkbox', { name: 'IBAN' })
    const creditCardCheckbox = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Credit card number' })
    expect(ibanCheckbox.checked).toBe(true)
    expect(creditCardCheckbox.checked).toBe(false)
  })

  it('saves the full entity set on submit after toggling a group off', async () => {
    apiFetchMock.mockResolvedValue({
      entities: ['EMAIL_ADDRESS', 'IBAN_CODE', 'CREDIT_CARD', 'NL_BTW', 'NL_KVK', 'NL_POSTCODE'],
    })
    renderSection()

    await waitFor(() => expect(screen.getByText('Contact details')).toBeTruthy())

    // Turn the contact group off (was fully on).
    fireEvent.click(screen.getByRole('checkbox', { name: 'Contact details' }))

    const saveButton = screen.getByRole('button', { name: 'Save' })
    fireEvent.click(saveButton)

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith('/api/orgs/me/pii-entities', {
        method: 'PATCH',
        body: JSON.stringify({
          entities: ['CREDIT_CARD', 'IBAN_CODE', 'NL_BTW', 'NL_KVK', 'NL_POSTCODE'],
        }),
      })
    })
  })

  it('renders read-only for a caller below the admin role', async () => {
    fetchMeMock.mockResolvedValue({ portal_role: 'kb_manager', is_platform_admin: false })
    renderSection()

    await waitFor(() => {
      expect(screen.getByText('Only organisation admins can change this.')).toBeTruthy()
    })
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    const contactCheckbox = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Contact details' })
    expect(contactCheckbox.disabled).toBe(true)
  })

  it('shows all seven REQ-13 limitation points, worded additively', () => {
    renderSection()

    expect(screen.getByText('What this does and does not catch')).toBeTruthy()
    expect(screen.getByText('Names are not detected.')).toBeTruthy()
    expect(screen.getByText('Addresses are recognised by postcode, not by street name.')).toBeTruthy()
    expect(screen.getByText('Detection covers structured identifiers.')).toBeTruthy()
    expect(screen.getByText('The context around a masked value stays.')).toBeTruthy()
    expect(screen.getByText('False positives happen.')).toBeTruthy()
    expect(screen.getByText('This only governs what is sent to the AI model.')).toBeTruthy()
    expect(screen.getByText('Two categories cannot be switched off.')).toBeTruthy()

    // Tone rules (REQ-13): never claim the result is anonymous, never claim
    // enabling this makes the tenant compliant.
    expect(screen.queryByText(/anonymous/i)).toBeNull()
    expect(screen.queryByText(/compliant/i)).toBeNull()
  })
})
