import { describe, expect, it } from 'vitest'

import { buildCrawlerCookies } from '../-connector-shared/api'
import {
  CookieJsonError,
  isManualRow,
  ownerHost,
  parseCookieEditorJson,
} from '../-connector-shared/cookieHosts'

const SECRET = 'not-a-real-cookie-value'

const EXPORT = JSON.stringify([
  {
    name: 'idp_session',
    value: SECRET,
    domain: 'idp.example.com',
    path: '/',
    expirationDate: 1798761600.5,
    secure: true,
    httpOnly: true,
  },
  { name: 'theme', value: 'dark', domain: '.example.com', session: true },
])

describe('parseCookieEditorJson', () => {
  it('turns a Cookie-Editor export into rows with name, domain and expiry', () => {
    const rows = parseCookieEditorJson(EXPORT, 'app.example.com')
    expect(rows.map((r) => [r.name, r.domain])).toEqual([
      ['idp_session', 'idp.example.com'],
      ['theme', '.example.com'],
    ])
    expect(rows[0].expires).toBe('2027-01-01T00:00:00.500Z')
    expect(rows[0].httpOnly).toBe(true)
    expect(rows[1].expires).toBeUndefined()
  })

  it('refuses a paste that is not an array of named cookies with values', () => {
    expect(() => parseCookieEditorJson('nope', 'x.example.com')).toThrow(CookieJsonError)
    expect(() => parseCookieEditorJson('{"name":"a"}', 'x.example.com')).toThrow(/array/)
    expect(() => parseCookieEditorJson('[{"name":"a"}]', 'x.example.com')).toThrow(/no value/)
  })

  it('does not echo a value in the error for a bad cookie', () => {
    try {
      parseCookieEditorJson(`[{"value":"${SECRET}"}]`, 'x.example.com')
    } catch (error) {
      expect((error as Error).message).not.toContain(SECRET)
    }
  })
})

describe('cookie ownership', () => {
  const rows = parseCookieEditorJson(EXPORT, 'app.example.com')
  const chain = ['idp.example.com']

  it('puts a cookie on the sign-in host it names, and a parent-domain cookie on the base host', () => {
    expect(ownerHost(rows[0], 'app.example.com', chain)).toBe('idp.example.com')
    expect(ownerHost(rows[1], 'app.example.com', chain)).toBe('app.example.com')
  })

  it('keeps hand-typed rows as the name/value inputs of the base host', () => {
    expect(isManualRow({ name: 'a', value: '' }, 'app.example.com', chain)).toBe(true)
    expect(isManualRow(rows[1], 'app.example.com', chain)).toBe(false)
    expect(isManualRow({ name: 'saved', value: '', domain: 'idp.example.com' }, 'app.example.com', chain)).toBe(false)
  })
})

describe('buildCrawlerCookies with per-host cookies', () => {
  it('keeps each cookie on its own domain and sends the expiry of an imported one', () => {
    const rows = parseCookieEditorJson(EXPORT, 'app.example.com')
    const built = buildCrawlerCookies([{ name: 'typed', value: 'v' }, ...rows], 'https://app.example.com') as Record<
      string,
      unknown
    >[]
    expect(built.map((c) => c.domain)).toEqual(['app.example.com', 'idp.example.com', '.example.com'])
    expect(built[1].expires).toBe('2027-01-01T00:00:00.500Z')
    expect('expires' in built[0]).toBe(false)
  })
})
