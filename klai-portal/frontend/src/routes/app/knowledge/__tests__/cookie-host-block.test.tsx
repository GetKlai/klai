/**
 * The auth step asks for cookies per host: a block for each sign-in host the
 * site redirects through, fed by a Cookie-Editor JSON paste that is listed by
 * name and expiry and never by value.
 */

import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import type { CookieRow } from '../$kbSlug/-kb-types'
import { CrawlerAuthSetupStep } from '../-connector-shared/CrawlerAuthSetupStep'

vi.mock('../-connector-shared/api', async () => {
  const actual = await vi.importActual<typeof import('../-connector-shared/api')>('../-connector-shared/api')
  return { ...actual, detectAuthChainHosts: vi.fn().mockResolvedValue(['idp.example.com']) }
})

const SECRET = 'not-a-real-cookie-value'

function Harness({ onRows }: { onRows: (rows: CookieRow[]) => void }) {
  const [rows, setRows] = useState<CookieRow[]>([])
  const [hosts, setHosts] = useState<string[]>([])
  return (
    <CrawlerAuthSetupStep
      kbSlug="handbook"
      baseUrl="https://app.example.com/"
      authChainHosts={hosts}
      onAuthChainHostsDetected={setHosts}
      testUrl=""
      onTestUrlChange={() => {}}
      mode={{
        kind: 'cookies',
        idPrefix: 'test-cookie',
        rows,
        onChange: (next) => {
          setRows(next)
          onRows(next)
        },
      }}
      isPending={false}
      error={null}
      result={null}
      onProbe={() => {}}
      onNext={() => {}}
      onBack={() => {}}
    />
  )
}

describe('cookies per sign-in host', () => {
  it('shows a block for the detected sign-in host and lists pasted cookies by name and expiry only', async () => {
    const onRows = vi.fn()
    render(<Harness onRows={onRows} />)

    expect(await screen.findByText(/signs in via idp\.example\.com/)).toBeTruthy()

    const paste = JSON.stringify([
      { name: 'idp_session', value: SECRET, domain: 'idp.example.com', path: '/', expirationDate: 1798761600 },
    ])
    fireEvent.change(screen.getByLabelText(/for idp\.example\.com/), { target: { value: paste } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Add cookies' })[1])

    await waitFor(() => expect(screen.getByText('idp_session')).toBeTruthy())
    expect(screen.getByText(/expires/)).toBeTruthy()
    expect(document.body.textContent).not.toContain(SECRET)
    const rows = onRows.mock.lastCall?.[0] as CookieRow[]
    expect(rows.map((r) => [r.name, r.domain])).toEqual([['idp_session', 'idp.example.com']])
  })

  it('refuses a cookie for a host the site does not sign in through', async () => {
    render(<Harness onRows={() => {}} />)
    await screen.findByText(/signs in via idp\.example\.com/)

    const paste = JSON.stringify([{ name: 'other', value: SECRET, domain: 'elsewhere.example.org' }])
    fireEvent.change(screen.getByLabelText(/for app\.example\.com/), { target: { value: paste } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Add cookies' })[0])

    expect(await screen.findByText(/another site: other \(elsewhere\.example\.org\)/)).toBeTruthy()
    expect(screen.queryByText('other')).toBeNull()
  })
})
