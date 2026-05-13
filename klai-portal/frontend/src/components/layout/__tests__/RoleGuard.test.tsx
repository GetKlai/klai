import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RoleGuard } from '../RoleGuard'
import type { CurrentUser } from '@/hooks/useCurrentUser'

// Mock the messages module
vi.mock('@/paraglide/messages', () => ({
  product_guard_cta: () => 'Contact your admin to upgrade',
  role_guard_description: ({ minRole }: { minRole: string }) => `Requires ${minRole} or higher`,
}))

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
    portal_role: effective_role,
    products: [],
    isAdmin: effective_role === 'admin',
    isGroupAdmin: effective_role === 'group_manager',
    capabilities: [],
    effective_capabilities: [],
    effective_role,
    hasCapability: () => false,
  }
}

describe('RoleGuard', () => {
  const ROLES = ['personal', 'company', 'kb_manager', 'group_manager', 'admin'] as const

  it('shows children when user role meets minRole', () => {
    ROLES.forEach((role, idx) => {
      ROLES.slice(idx).forEach((higherOrEqual) => {
        mockUser = makeUser(higherOrEqual)
        const { unmount } = render(
          <RoleGuard minRole={role}>
            <div data-testid="content">Protected content</div>
          </RoleGuard>,
        )
        expect(screen.getByTestId('content')).toBeTruthy()
        unmount()
      })
    })
  })

  it('shows locked panel when user role is below minRole', () => {
    mockUser = makeUser('personal')
    render(
      <RoleGuard minRole="kb_manager">
        <div data-testid="content">Protected content</div>
      </RoleGuard>,
    )
    expect(screen.queryByTestId('content')).toBeNull()
    expect(screen.getByText(/Requires kb_manager or higher/i)).toBeTruthy()
  })

  it('shows locked panel when user is loading (effective_role undefined)', () => {
    mockUser = undefined
    render(
      <RoleGuard minRole="kb_manager">
        <div data-testid="content">Protected content</div>
      </RoleGuard>,
    )
    expect(screen.queryByTestId('content')).toBeNull()
  })

  it('shows content for admin when minRole is admin', () => {
    mockUser = makeUser('admin')
    render(
      <RoleGuard minRole="admin">
        <div data-testid="content">Admin only</div>
      </RoleGuard>,
    )
    expect(screen.getByTestId('content')).toBeTruthy()
  })

  it('blocks group_manager from admin-only content', () => {
    mockUser = makeUser('group_manager')
    render(
      <RoleGuard minRole="admin">
        <div data-testid="content">Admin only</div>
      </RoleGuard>,
    )
    expect(screen.queryByTestId('content')).toBeNull()
  })
})
