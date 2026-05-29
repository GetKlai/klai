import { describe, expect, it } from 'vitest'
import { deriveIsAdmin } from '../useCurrentUser'

describe('deriveIsAdmin', () => {
  it('treats portal admins as admins even when Zitadel roles are absent', () => {
    expect(deriveIsAdmin({ portal_role: 'admin', roles: [] })).toBe(true)
  })

  it('keeps existing Zitadel admin-role detection', () => {
    expect(deriveIsAdmin({ portal_role: 'personal', roles: ['org:owner'] })).toBe(true)
  })

  it('does not mark normal users as admins', () => {
    expect(deriveIsAdmin({ portal_role: 'personal', roles: [] })).toBe(false)
  })
})
