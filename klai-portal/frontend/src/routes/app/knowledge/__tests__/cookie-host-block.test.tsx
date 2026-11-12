/**
 * The auth step asks for cookies per host: a block for each sign-in host the
 * site redirects through, fed by a Cookie-Editor JSON paste that is listed by
 * name and expiry and never by value. The test result says whether Klai can
 * sign in again by itself.
 */

import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import type { CookieRow } from '../$kbSlug/-kb-types'
import type { AuthProbeResult } from '../-connector-types'
import type { CrawlerAuthPayload } from '../-connector-shared/api'
import { detectAuthChainHosts } from '../-connector-shared/api'
import { CrawlerAuthSetupStep } from '../-connector-shared/CrawlerAuthSetupStep'

vi.mock('../-connector-shared/api', async () => {
  const actual = await vi.importActual<typeof import('../-connector-shared/api')>('../-connector-shared/api')
  return { ...actual, detectAuthChainHosts: vi.fn().mockResolvedValue(['idp.example.com']) }
})

const SECRET = 'not-a-real-cookie-value'

function Harness({
  onRows = () => {},
  onProbe = () => {},
  result = null,
}: {
  onRows?: (rows: CookieRow[]) => void
  onProbe?: (payload: CrawlerAuthPayload) => void
  result?: AuthProbeResult | null
}) {
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
        idPrefix: 'wc-cookie',
        rows,
        onChange: (next) => {
          setRows(next)
          onRows(next)
        },
      }}
      isPending={false}
      error={null}
      result={result}
      onProbe={onProbe}
      onNext={() => {}}
      onBack={() => {}}
    />
  )
}

describe('cookies per sign-in host', () => {
  it('asks for the sign-in host cookies before any test, and sends that host with the test', async () => {
    const onProbe = vi.fn()
    render(<Harness onProbe={onProbe} />)

    // Before the test ran: the site's own cookies pass on the day they are
    // pasted, so a box shown only after a failed test was never filled in.
    expect(await screen.findByText(/signs in via idp\.example\.com/)).toBeTruthy()

    const paste = JSON.stringify([
      { name: 'idp_session', value: SECRET, domain: 'idp.example.com', path: '/', expirationDate: 1798761600 },
    ])
    fireEvent.change(screen.getByLabelText(/for idp\.example\.com/), { target: { value: paste } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Add cookies' })[1])

    await waitFor(() => expect(screen.getByText('idp_session')).toBeTruthy())
    expect(screen.getByText(/expires/)).toBeTruthy()
    expect(document.body.textContent).not.toContain(SECRET)

    fireEvent.click(screen.getByRole('button', { name: 'Test authentication' }))
    const payload = onProbe.mock.lastCall?.[0] as CrawlerAuthPayload
    expect(payload.auth_chain_hosts).toEqual(['idp.example.com'])
    expect((payload.cookies as CookieRow[]).map((c) => c.domain)).toEqual(['idp.example.com'])
  })

  it('refuses a cookie for a host the site does not sign in through', async () => {
    render(<Harness />)
    await screen.findByText(/signs in via idp\.example\.com/)

    const paste = JSON.stringify([{ name: 'other', value: SECRET, domain: 'elsewhere.example.org' }])
    fireEvent.change(screen.getByLabelText(/for app\.example\.com/), { target: { value: paste } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Add cookies' })[0])

    expect(await screen.findByText(/another site: other \(elsewhere\.example\.org\)/)).toBeTruthy()
    expect(screen.queryByText('other')).toBeNull()
  })

  const cannotRenew: AuthProbeResult = {
    classification: 'auth_ok',
    match_reasons: [],
    word_count: 300,
    auth_guard: null,
    renewal: { can_renew: false, reason: 'no_cookies_for:idp.example.com' },
  }

  it('after a good login that cannot renew, says why in plain words and shows the host block with how to copy', async () => {
    render(<Harness result={cannotRenew} />)
    expect(await screen.findByText(/To stay logged in, Klai also needs the cookies of idp\.example\.com/)).toBeTruthy()
    expect(screen.getByText(/Paste them in the field for idp\.example\.com above/)).toBeTruthy()
    expect(screen.getByLabelText(/for idp\.example\.com/)).toBeTruthy()
    expect(screen.getByText(/Open idp\.example\.com in the same browser/)).toBeTruthy()
    expect(document.body.textContent).not.toContain('no_cookies_for')
  })

  it('shows the host block when only the test result names the sign-in host', async () => {
    vi.mocked(detectAuthChainHosts).mockResolvedValueOnce([])
    render(<Harness result={cannotRenew} />)
    expect(await screen.findByLabelText(/for idp\.example\.com/)).toBeTruthy()
  })

  it('with saved cookies, points at Replace cookies because the host field only exists after it', async () => {
    render(
      <CrawlerAuthSetupStep
        kbSlug="handbook"
        baseUrl="https://app.example.com/"
        authChainHosts={['idp.example.com']}
        onAuthChainHostsDetected={() => {}}
        testUrl=""
        onTestUrlChange={() => {}}
        mode={{ kind: 'saved', onReplace: () => {}, onUseWithoutLogin: () => {} }}
        isPending={false}
        error={null}
        result={cannotRenew}
        onProbe={() => {}}
        onNext={() => {}}
        onBack={() => {}}
      />,
    )
    expect(await screen.findByText(/First choose 'Replace cookies'/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Replace cookies' })).toBeTruthy()
  })
})
