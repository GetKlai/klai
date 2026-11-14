/**
 * The auth step takes a Cookie-Editor JSON paste for the site's host, lists
 * the pasted cookies by name and expiry and never by value.
 */

import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import type { CookieRow } from '../$kbSlug/-kb-types'
import { CrawlerAuthSetupStep } from '../-connector-shared/CrawlerAuthSetupStep'

const SECRET = 'not-a-real-cookie-value'

function Harness({ onRows }: { onRows: (rows: CookieRow[]) => void }) {
  const [rows, setRows] = useState<CookieRow[]>([])
  return (
    <CrawlerAuthSetupStep
      baseUrl="https://app.example.com/"
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

describe('cookie paste for the site host', () => {
  it('lists pasted cookies by name and expiry only', async () => {
    const onRows = vi.fn()
    render(<Harness onRows={onRows} />)

    const paste = JSON.stringify([
      { name: 'app_session', value: SECRET, domain: 'app.example.com', path: '/', expirationDate: 1798761600 },
    ])
    fireEvent.change(screen.getByLabelText(/for app\.example\.com/), { target: { value: paste } })
    fireEvent.click(screen.getByRole('button', { name: 'Add cookies' }))

    await waitFor(() => expect(screen.getByText('app_session')).toBeTruthy())
    expect(screen.getByText(/expires/)).toBeTruthy()
    expect(document.body.textContent).not.toContain(SECRET)
    const rows = onRows.mock.lastCall?.[0] as CookieRow[]
    expect(rows.map((r) => [r.name, r.domain])).toEqual([['app_session', 'app.example.com']])
  })

  it('refuses a cookie for another host', async () => {
    render(<Harness onRows={() => {}} />)

    const paste = JSON.stringify([{ name: 'other', value: SECRET, domain: 'elsewhere.example.org' }])
    fireEvent.change(screen.getByLabelText(/for app\.example\.com/), { target: { value: paste } })
    fireEvent.click(screen.getByRole('button', { name: 'Add cookies' }))

    expect(await screen.findByText(/another site: other \(elsewhere\.example\.org\)/)).toBeTruthy()
    expect(screen.queryByText('other')).toBeNull()
  })
})
