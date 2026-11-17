import { render } from '@testing-library/react'
import { beforeAll, describe, expect, it } from 'vitest'
import { setLocale } from '@/paraglide/runtime'

import { CrawlCoverageNote } from '../-sources-helpers'
import type { CrawlCoverage, Source } from '../-sources-types'

const base: CrawlCoverage = {
  pages_read: 80,
  addresses_gone: 3,
  addresses_waiting: 120,
  addresses_known: 203,
  max_pages: 80,
  stopped_by: null,
}

function source(coverage: Partial<CrawlCoverage> | null, overrides: Partial<Source> = {}): Source {
  return {
    kind: 'connector',
    id: 'conn-1',
    name: 'Docs site',
    type_label: "Website (pagina's)",
    connector_type: 'web_crawler',
    items_count: 80,
    chunks_count: 200,
    status: 'completed',
    last_sync_at: '2026-11-17T01:00:00Z',
    created_at: null,
    sync_coverage: coverage ? { ...base, ...coverage } : null,
    ...overrides,
  }
}

function text(s: Source): string {
  return render(<CrawlCoverageNote source={s} />).container.textContent ?? ''
}

const cases: Array<[string, Partial<CrawlCoverage>, Record<'nl' | 'en', string>]> = [
  [
    'page limit',
    { stopped_by: 'page_limit' },
    {
      nl: "Deze nacht zijn 80 van 200 pagina's gelezen. De rest volgt de komende nachten; een volledige ronde duurt ongeveer 3 nachten. Je kunt het maximale aantal pagina's verhogen in de instellingen van deze bron.",
      en: "This night 80 of 200 pages were read. The rest follows in the next nights; a full round takes about 3 nights. You can raise the maximum number of pages in this source's settings.",
    },
  ],
  [
    'site rate limit',
    { stopped_by: 'site_rate_limit', pages_read: 30, addresses_waiting: 70 },
    {
      nl: "De site vertraagde ons na 30 pagina's. De rest volgt de komende nachten; een volledige ronde duurt ongeveer 4 nachten.",
      en: 'The site slowed us down after 30 pages. The rest follows in the next nights; a full round takes about 4 nights.',
    },
  ],
  [
    'attempt limit',
    { stopped_by: 'attempt_limit' },
    {
      nl: "De paginalijst van deze site bestaat grotendeels uit pagina's die niet bestaan, dus we konden niet alles lezen. Dat kan de beheerder van de site oplossen.",
      en: "The site's page list consists mostly of pages that do not exist, so we could not read everything. The owner of the site can fix that.",
    },
  ],
  [
    'everything visited but most known addresses do not exist',
    { addresses_waiting: 0, addresses_gone: 150, addresses_known: 230, pages_read: 80 },
    {
      nl: "De paginalijst van deze site noemt 150 pagina's die niet bestaan. Dat kan de beheerder van de site oplossen; aan onze kant is niets mis.",
      en: "The site's own page list names 150 pages that do not exist. The owner of the site can fix that; nothing is wrong on our side.",
    },
  ],
]

describe('CrawlCoverageNote', () => {
  describe.each(['nl', 'en'] as const)('in %s', (locale) => {
    beforeAll(() => setLocale(locale, { reload: false }))

    it.each(cases)('says the right thing for: %s', (_name, coverage, expected) => {
      expect(text(source(coverage))).toBe(expected[locale])
    })
  })

  it('adds nothing when everything was visited and few addresses are gone', () => {
    expect(text(source({ addresses_waiting: 0, addresses_gone: 10, addresses_known: 90 }))).toBe('')
  })

  it('adds nothing without coverage or for another source kind', () => {
    expect(text(source(null))).toBe('')
    expect(text(source({ stopped_by: 'page_limit' }, { kind: 'upload', connector_type: null }))).toBe('')
  })
})
