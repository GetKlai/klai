import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useEffectiveRole } from '../useEffectiveRole'
import type { CurrentUser } from '../useCurrentUser'

let mockUser: CurrentUser | undefined = undefined

vi.mock('@/hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ user: mockUser }),
}))

function makeUser(effective_role: string): CurrentUser {
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
    capabilities: [],
    effective_capabilities: [],
    effective_role,
    hasCapability: () => false,
  }
}

describe('useEffectiveRole', () => {
  it('returns undefined when user is not loaded', () => {
    mockUser = undefined
    const { result } = renderHook(() => useEffectiveRole())
    expect(result.current).toBeUndefined()
  })

  it('returns the effective_role from user data', () => {
    mockUser = makeUser('kb_manager')
    const { result } = renderHook(() => useEffectiveRole())
    expect(result.current).toBe('kb_manager')
  })

  it('returns admin effective_role for admin users', () => {
    mockUser = makeUser('admin')
    const { result } = renderHook(() => useEffectiveRole())
    expect(result.current).toBe('admin')
  })
})
