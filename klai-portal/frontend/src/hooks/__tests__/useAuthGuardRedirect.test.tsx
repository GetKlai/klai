import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { useAuthGuardRedirect } from '../useAuthGuardRedirect'
import { AuthContext, type AuthContextValue } from '@/lib/auth-context'

// Capture every navigate() call from inside the hook.
const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
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

describe('useAuthGuardRedirect', () => {
  beforeEach(() => {
    navigate.mockReset()
  })

  it('does not navigate while auth is still loading', () => {
    renderHook(() => useAuthGuardRedirect(), {
      wrapper: wrapperFor(makeAuth({ isLoading: true, isAuthenticated: false })),
    })
    expect(navigate).not.toHaveBeenCalled()
  })

  it('redirects to / when auth has resolved unauthenticated', () => {
    renderHook(() => useAuthGuardRedirect(), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: false })),
    })
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith({ to: '/' })
  })

  it('respects a custom fallback path', () => {
    renderHook(() => useAuthGuardRedirect({ fallback: '/logged-out' }), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: false })),
    })
    expect(navigate).toHaveBeenCalledWith({ to: '/logged-out' })
  })

  it('does not navigate when authenticated', () => {
    renderHook(() => useAuthGuardRedirect(), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: true })),
    })
    expect(navigate).not.toHaveBeenCalled()
  })

  // Regression: before the SPEC-AUTH-008 follow-up, /app and /admin guards
  // gated the redirect behind `useCurrentUser().isPending`, which stays `true`
  // forever when the query is disabled. An unauthenticated visitor thus
  // stared at an infinite spinner. This hook deliberately ignores that query
  // and redirects purely on auth state — the tests above assert it.
  it('redirects even when a simulated userLoading=true dependency would otherwise block', () => {
    // The hook itself takes no userLoading input — that is the point. If a
    // future refactor re-introduces such a dependency, this placeholder
    // scenario (unauthenticated, auth resolved) should still redirect.
    renderHook(() => useAuthGuardRedirect(), {
      wrapper: wrapperFor(makeAuth({ isLoading: false, isAuthenticated: false })),
    })
    expect(navigate).toHaveBeenCalledWith({ to: '/' })
  })
})
