/* @vitest-environment jsdom */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { KlaiAuthProvider, useAuth } from '@/lib/auth'

const originalFetch = globalThis.fetch

function mockLocation() {
  const assigned: { current: string | null } = { current: null }
  const descriptor = Object.getOwnPropertyDescriptor(window, 'location')
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: new Proxy(
      { href: 'https://voys.getklai.com/app' },
      {
        set(target: Record<string, string>, prop: string, value: string) {
          if (prop === 'href') assigned.current = value
          target[prop] = value
          return true
        },
        get(target: Record<string, string>, prop: string) {
          return target[prop] ?? ''
        },
      },
    ),
  })
  return {
    assigned,
    restore: () => {
      if (descriptor) Object.defineProperty(window, 'location', descriptor)
    },
  }
}

function mockCsrfCookie(value: string) {
  Object.defineProperty(document, 'cookie', {
    configurable: true,
    get: () => `__Secure-klai_csrf=${value}`,
  })
}

function wrapper({ children }: { children: ReactNode }) {
  return <KlaiAuthProvider>{children}</KlaiAuthProvider>
}

describe('BffAuthProvider.removeUser', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  let location: ReturnType<typeof mockLocation>

  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
    location = mockLocation()
    mockCsrfCookie('csrf-xyz')
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    location.restore()
    vi.restoreAllMocks()
  })

  it('posts to /api/auth/bff/logout with the CSRF header and navigates to the RP-initiated end_session URL', async () => {
    // /api/auth/session -> 401 (unauthenticated bootstrap). /bff/logout -> 204 with postLogout.
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(null, {
          status: 204,
          headers: { 'X-Post-Logout-Redirect': 'https://auth.getklai.com/oidc/v1/end_session?x=1' },
        }),
      )

    const { result } = renderHook(() => useAuth(), { wrapper })
    // Wait for the initial /api/auth/session probe to settle.
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      await result.current.removeUser()
    })

    // First call: session probe. Second call: logout.
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [logoutUrl, logoutInit] = fetchMock.mock.calls[1] as [string, RequestInit & { headers: Record<string, string> }]
    expect(logoutUrl).toBe('/api/auth/bff/logout')
    expect(logoutInit.method).toBe('POST')
    expect(logoutInit.credentials).toBe('include')
    expect(logoutInit.headers['X-CSRF-Token']).toBe('csrf-xyz')
    expect(location.assigned.current).toBe('https://auth.getklai.com/oidc/v1/end_session?x=1')
  })

  it('falls back to /logged-out when the backend does not return X-Post-Logout-Redirect', async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      await result.current.removeUser()
    })

    expect(location.assigned.current).toBe('/logged-out')
  })

  it('is a no-op while a previous logout is still in flight (sticky isSigningOut ref)', async () => {
    let resolveLogout: (r: Response) => void = () => {}
    const pendingLogout = new Promise<Response>((r) => {
      resolveLogout = r
    })
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // session bootstrap
      .mockReturnValueOnce(pendingLogout) // first logout — never resolves yet

    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await Promise.resolve()
    })

    // Fire two logouts back-to-back before the first completes.
    act(() => {
      void result.current.removeUser()
      void result.current.removeUser()
    })

    // Second call must be deduplicated — still only one POST initiated.
    expect(fetchMock).toHaveBeenCalledTimes(2) // 1 session probe + 1 logout
    const logoutCalls = fetchMock.mock.calls.filter(
      (call) => call[0] === '/api/auth/bff/logout',
    )
    expect(logoutCalls).toHaveLength(1)

    // Release the inflight logout so the test suite can settle.
    await act(async () => {
      resolveLogout(new Response(null, { status: 204 }))
      await Promise.resolve()
    })
  })

  it('navigates to /logged-out and clears user state when the logout fetch itself throws', async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // session bootstrap
      .mockRejectedValueOnce(new TypeError('network down'))

    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      await result.current.removeUser()
    })

    expect(location.assigned.current).toBe('/logged-out')
  })
})
