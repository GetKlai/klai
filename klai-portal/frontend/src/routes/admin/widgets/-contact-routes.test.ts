import { describe, it, expect } from 'vitest'
import {
  MAX_ROUTES,
  addRoute,
  issuesToErrors,
  removeRoute,
  setDefault,
  toDrafts,
  toRoutes,
  validateRoute,
  validateRoutes,
  type RouteDraft,
} from './-contact-routes'

function filled(overrides: Partial<RouteDraft> = {}): RouteDraft {
  const [draft] = addRoute([])
  return { ...draft, name: 'Support', description: 'Product problems', email: 'team@example.com', ...overrides }
}

describe('contact routes: the default', () => {
  it('makes the first added route the default and later ones not', () => {
    const list = addRoute(addRoute([]))
    expect(list.map((d) => d.is_default)).toEqual([true, false])
  })

  it('keeps exactly one default when another route is chosen', () => {
    const list = addRoute(addRoute([]))
    const next = setDefault(list, list[1].key)
    expect(next.map((d) => d.is_default)).toEqual([false, true])
  })

  it('hands the default to the first remaining route when the default is deleted', () => {
    const list = addRoute(addRoute(addRoute([])))
    const next = removeRoute(list, list[0].key)
    expect(next).toHaveLength(2)
    expect(next.filter((d) => d.is_default)).toHaveLength(1)
    expect(next[0].is_default).toBe(true)
  })

  it('rejects a list without a default or with two', () => {
    const a = filled()
    const b = filled({ is_default: false })
    expect(validateRoutes([{ ...a, is_default: false }, b]).ok).toBe(false)
    expect(validateRoutes([a, { ...b, is_default: true }]).ok).toBe(false)
    expect(validateRoutes([a, b]).ok).toBe(true)
    expect(validateRoutes([]).ok).toBe(true)
  })
})

describe('contact routes: the six-route cap', () => {
  it('does not add a seventh route', () => {
    let list: RouteDraft[] = []
    for (let i = 0; i < MAX_ROUTES + 2; i++) list = addRoute(list)
    expect(list).toHaveLength(MAX_ROUTES)
  })
})

describe('contact routes: link and channels', () => {
  it('requires link address and link text together', () => {
    expect(validateRoute(filled({ link_url: 'https://example.com/form' })).link_label).toEqual({ code: 'link_pair' })
    expect(validateRoute(filled({ link_label: 'Form' })).link_url).toEqual({ code: 'link_pair' })
    expect(validateRoute(filled({ link_url: 'https://example.com/form', link_label: 'Form' }))).toEqual({})
  })

  it('rejects a phone number with fewer than six digits or stray characters', () => {
    expect(validateRoute(filled({ phone: '12 34' })).phone).toEqual({ code: 'phone' })
    expect(validateRoute(filled({ phone: 'call me' })).phone).toEqual({ code: 'phone' })
    expect(validateRoute(filled({ phone: '+31 (0)20 123 4567' })).phone).toBeUndefined()
  })

  it('accepts only absolute http(s) booking addresses', () => {
    expect(validateRoute(filled({ booking_url: 'example.com/book' })).booking_url).toEqual({ code: 'url' })
    expect(validateRoute(filled({ booking_url: 'javascript:alert(1)' })).booking_url).toEqual({ code: 'url' })
    expect(validateRoute(filled({ booking_url: 'https://example.com/book' })).booking_url).toBeUndefined()
  })
})

describe('contact routes: ids and the payload', () => {
  it('sends the id of an existing route back unchanged and none for a new route', () => {
    const drafts = toDrafts([{ id: 'route-abc', name: 'Billing', description: 'Invoices', is_default: true }])
    const edited = [{ ...drafts[0], name: 'Billing team' }, ...addRoute([]).map((d) => ({ ...d, name: 'Sales', is_default: false }))]
    const routes = toRoutes(edited)
    expect(routes[0].id).toBe('route-abc')
    expect(routes[0].name).toBe('Billing team')
    expect('id' in routes[1]).toBe(false)
  })

  it('leaves empty optional channels out of the payload', () => {
    const [route] = toRoutes([filled({ email: ' team@example.com ' })])
    expect(route.email).toBe('team@example.com')
    expect(route.phone).toBeUndefined()
    expect(route.referral_text).toBeUndefined()
  })

  it('sends the referral text trimmed and rejects one over 500 characters', () => {
    const [route] = toRoutes([filled({ referral_text: ' Please call us, we will help. ' })])
    expect(route.referral_text).toBe('Please call us, we will help.')
    expect(validateRoute(filled({ referral_text: 'x'.repeat(501) })).referral_text).toEqual({ code: 'too_long', max: 500 })
  })
})

describe('contact routes: a route nobody can reach', () => {
  it('needs a channel or a referral text', () => {
    expect(validateRoute(filled({ email: '' })).route).toEqual({ code: 'unreachable' })
    expect(validateRoute(filled()).route).toBeUndefined()
    expect(validateRoute(filled({ email: '', referral_text: 'Please get in touch.' })).route).toBeUndefined()
  })
})

describe('contact routes: the API 422', () => {
  it('shows an error that stops at the route index on that route, not as a general message', () => {
    const drafts = [filled(), filled({ is_default: false })]
    const { byRoute, general } = issuesToErrors(drafts, [
      { loc: ['body', 'widget_config', 'contact_routes', 1], msg: 'route unreachable', type: 'value_error' },
    ])
    expect(byRoute[drafts[1].key].route).toEqual({ code: 'server', message: 'route unreachable' })
    expect(general).toEqual([])
  })

  it('shows a server message at the field it names and keeps the rest general', () => {
    const drafts = [filled(), filled({ is_default: false })]
    const { byRoute, general } = issuesToErrors(drafts, [
      { loc: ['body', 'widget_config', 'contact_routes', 1, 'phone'], msg: 'bad phone', type: 'value_error' },
      { loc: ['body', 'widget_config', 'contact_routes'], msg: 'need a default', type: 'value_error' },
    ])
    expect(byRoute[drafts[1].key].phone).toEqual({ code: 'server', message: 'bad phone' })
    expect(general).toEqual(['need a default'])
  })
})
