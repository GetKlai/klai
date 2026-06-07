import { describe, expect, it } from 'vitest'
import { Users } from 'lucide-react'
import type { MeResponse } from '@/lib/api-me'
import { adminNavItemIsVisible, type AdminNavItem } from '../-nav'

function item(overrides: Partial<AdminNavItem> = {}): AdminNavItem {
  return {
    to: '/admin/users',
    label: 'Users',
    icon: Users,
    minRole: 'admin',
    ...overrides,
  }
}

describe('adminNavItemIsVisible', () => {
  it('applies the same role gates for overview tiles and sidebar items', () => {
    expect(adminNavItemIsVisible(item({ minRole: 'group_manager' }), 'group_manager', undefined)).toBe(true)
    expect(adminNavItemIsVisible(item({ minRole: 'admin' }), 'group_manager', undefined)).toBe(false)
  })

  it('keeps feature-gated items visible while /api/me loads, then follows tenant unlocks', () => {
    const widgets = item({ to: '/admin/widgets', requiresFeature: 'widgets' })
    const withoutWidgets: MeResponse = { platform_unlocked_features: [] }
    const withWidgets: MeResponse = { platform_unlocked_features: ['widgets'] }

    expect(adminNavItemIsVisible(widgets, 'admin', undefined)).toBe(true)
    expect(adminNavItemIsVisible(widgets, 'admin', withoutWidgets)).toBe(false)
    expect(adminNavItemIsVisible(widgets, 'admin', withWidgets)).toBe(true)
  })

  it('shows platform-only items only to platform admins', () => {
    const platform = item({ to: '/admin/platform', platformAdminOnly: true })

    expect(adminNavItemIsVisible(platform, 'admin', { is_platform_admin: false })).toBe(false)
    expect(adminNavItemIsVisible(platform, 'admin', { is_platform_admin: true })).toBe(true)
  })
})
