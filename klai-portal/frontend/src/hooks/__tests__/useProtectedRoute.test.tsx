import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { useProtectedRoute } from '../useProtectedRoute'
import { AuthContext, type AuthContextValue } from '@/lib/auth-context'
import type { CurrentUser } from '../useCurrentUser'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

// `useCurrentUser` is mocked via a settable query state — each test overrides
// the payload before rendering the hook.
interface MockQuery {
  user: CurrentUser | undefined
  isPending: boolean
}
let mockQuery: MockQuery = { user: undefined, isPending: true }
vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => mockQuery,
}))

type AuthOverrides = {
  isLoading?: boolean
  isAuthenticated?: boolean
}

function makeAuth({ isLoading = false, isAuthenticated = true }: AuthOverrides = {}): AuthContextValue {
  return {
    isLoading,
    isAuthenticated,
    user: isAuthenticated
      ? { profile: { sub: 'u1' }, csrf_token: 'x', access_token_expires_at: 0 }
      : null,
    error: null,
    signinRedirect: () => Promise.resolve(),
    removeUser: () => Promise.resolve(),
    signoutRedirect: () => Promise.resolve(),
    refetch: () => Promise.resolve(),
  }
}

function wrapperFor(auth: AuthContextValue) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
  }
}

function userFixture(partial: Partial<CurrentUser> = {}): CurrentUser {
  return {
    user_id: 'u1',
    email: 'x@klai.test',
    name: 'X',
    org_id: '1',
    roles: [],
    workspace_url: null,
    provisioning_status: 'ready',
    mfa_enrolled: true,
    mfa_policy: 'optional',
    preferred_language: 'en',
    portal_role: 'member',
    products: [],
    isAdmin: false,
    isGroupAdmin: false,
    requires_2fa_setup: false,
    ...partial,
  }
}

describe('useProtectedRoute', () => {
  beforeEach(() => {
    navigate.mockReset()
    mockQuery = { user: undefined, isPending: true }
    // Reset any lingering location.replace mock.
    vi.restoreAllMocks()
  })

  it('does not resolve while auth is still loading', () => {
    const { result } = renderHook(() => useProtectedRoute(), {
      wrapper: wrapperFor(makeAuth({ isLoading: true, isAuthenticated: false })),
    })
    expect(navigate).not.toHaveBeenCalled()
    expect(result.current.canRender).toBe(false)
    expect(result.current.isResolving).toBe(true)
  })

  it('redirects to / when unauthenticated — without waiting on the disabled /api/me query', () => {
    // Classic regression case: useCurrentUser is disabled (isPending: true forever)
    // because auth.isAuthenticated is false. The guard must still redirect.
    mockQuery = { user: undefined, isPending: true }
    const { result } = renderHook(() => useProtectedRoute(), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: false })),
    })
    expect(navigate).toHaveBeenCalledWith({ to: '/' })
    expect(result.current.canRender).toBe(false)
  })

  it('honours a custom fallback', () => {
    mockQuery = { user: undefined, isPending: true }
    renderHook(() => useProtectedRoute({ fallback: '/logged-out' }), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: false })),
    })
    expect(navigate).toHaveBeenCalledWith({ to: '/logged-out' })
  })

  it('stays resolving while useCurrentUser is loading', () => {
    mockQuery = { user: undefined, isPending: true }
    const { result } = renderHook(() => useProtectedRoute(), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: true })),
    })
    expect(navigate).not.toHaveBeenCalled()
    expect(result.current.canRender).toBe(false)
  })

  it('allows render when authenticated and user resolves without extra gates', () => {
    mockQuery = { user: userFixture(), isPending: false }
    const { result } = renderHook(() => useProtectedRoute(), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: true })),
    })
    expect(navigate).not.toHaveBeenCalled()
    expect(result.current.canRender).toBe(true)
    expect(result.current.user?.user_id).toBe('u1')
  })

  it('redirects to /setup/2fa when requires_2fa_setup is true', () => {
    const replace = vi.fn()
    vi.stubGlobal('location', { replace } as unknown as Location)
    mockQuery = { user: userFixture({ requires_2fa_setup: true }), isPending: false }
    renderHook(() => useProtectedRoute(), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: true })),
    })
    expect(replace).toHaveBeenCalledWith('/setup/2fa')
  })

  it('redirects to noRoleFallback when requireAdmin is true and user lacks the role', () => {
    mockQuery = { user: userFixture({ isAdmin: false, isGroupAdmin: false }), isPending: false }
    const { result } = renderHook(
      () => useProtectedRoute({ requireAdmin: true, noRoleFallback: '/app' }),
      { wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: true })) },
    )
    expect(navigate).toHaveBeenCalledWith({ to: '/app' })
    expect(result.current.canRender).toBe(false)
  })

  it('permits admins when requireAdmin is true', () => {
    mockQuery = { user: userFixture({ isAdmin: true }), isPending: false }
    const { result } = renderHook(
      () => useProtectedRoute({ requireAdmin: true, noRoleFallback: '/app' }),
      { wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: true })) },
    )
    expect(navigate).not.toHaveBeenCalled()
    expect(result.current.canRender).toBe(true)
  })

  it('permits group admins when requireAdmin is true', () => {
    mockQuery = { user: userFixture({ isGroupAdmin: true }), isPending: false }
    const { result } = renderHook(
      () => useProtectedRoute({ requireAdmin: true, noRoleFallback: '/app' }),
      { wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: true })) },
    )
    expect(navigate).not.toHaveBeenCalled()
    expect(result.current.canRender).toBe(true)
  })
})
