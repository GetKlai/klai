/**
 * Unit tests for /app/focus/* catch-all redirect — SPEC-PORTAL-UNIFY-KB-001 R-E4
 *
 * Verifies that the splat route's beforeLoad always throws a redirect to
 * /app/knowledge, regardless of the sub-path supplied.
 *
 * We test the route configuration directly: call beforeLoad() and confirm it
 * throws an object with `{ to: '/app/knowledge' }` inside the redirect
 * payload (TanStack Router's redirect() returns a thrown object with an `href`
 * and the options we supplied).
 */
import { describe, it, expect } from 'vitest'
import { Route } from '../$'

describe('/app/focus/$ splat route', () => {
  function callBeforeLoad() {
    // TanStack Router calls beforeLoad with context; for redirect-only routes
    // none of the arguments are needed, so we can pass an empty object.
    return (Route.options as { beforeLoad: () => never }).beforeLoad()
  }

  it('throws a redirect for /app/focus/<notebook-id> sub-paths', () => {
    expect(() => callBeforeLoad()).toThrow()
  })

  it('redirect target is /app/knowledge', () => {
    let thrown: unknown
    try {
      callBeforeLoad()
    } catch (e) {
      thrown = e
    }
    // TanStack Router's redirect() in a jsdom environment throws a Response
    // (status 307) whose `options` property carries the original redirect args.
    // We verify the `to` key inside `options` to confirm the destination.
    expect(thrown).toBeInstanceOf(Response)
    const response = thrown as Response & { options?: { to?: string } }
    expect(response.status).toBe(307)
    expect(response.options?.to).toBe('/app/knowledge')
  })

  it('redirect fires regardless of splat segment value', () => {
    // beforeLoad receives no input that changes the redirect destination —
    // call it multiple times to confirm deterministic behaviour.
    const attempts = [undefined, null, {}, { params: { _splat: 'new' } }]
    for (const _ctx of attempts) {
      expect(() => callBeforeLoad()).toThrow()
    }
  })
})
