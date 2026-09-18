/**
 * Support-case entry point on the Sources action bar is org-only: a personal
 * (user-owned) KB has no support inbox, and while ownership is still unknown we
 * must not offer a link that would land on an org-only screen.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children?: ReactNode }) => (
    <a href={typeof to === 'string' ? to : ''}>{children}</a>
  ),
}))

vi.mock('../-sources-hooks', () => ({
  useSyncAllConnectors: () => ({ mutate: vi.fn(), isPending: false }),
}))

import { SourcesActionBar } from '../-sources-actionbar'

function renderBar(showSupportCases: boolean) {
  return render(
    <SourcesActionBar
      kbSlug="company-kb"
      sources={[]}
      connectorSources={[]}
      showEditorLink={false}
      showSupportCases={showSupportCases}
    />,
  )
}

describe('SourcesActionBar support-cases link', () => {
  it('shows the support-cases link for an organisation KB', () => {
    renderBar(true)
    expect(screen.getByText('Support cases')).toBeTruthy()
  })

  it('hides the support-cases link when ownership is personal or still unknown', () => {
    renderBar(false)
    expect(screen.queryByText('Support cases')).toBeNull()
  })
})
