/**
 * RoleGuard — SPEC-PORTAL-PROFILES-001 P3.3
 *
 * Renders children when the current user's effective_role meets or exceeds
 * minRole on the five-rung profile ladder. Falls back to LockedPanel otherwise.
 *
 * Loading state: treats as "no access yet" (fail-closed) until user data resolves.
 */
import type { ReactNode } from 'react'
import * as m from '@/paraglide/messages'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { LockedPanel } from '@/components/layout/LockedPanel'
import { meetsMinRole, type ProfileRole } from '@/lib/profiles'

interface RoleGuardProps {
  minRole: ProfileRole
  children: ReactNode
}

export function RoleGuard({ minRole, children }: RoleGuardProps) {
  const { user } = useCurrentUser()
  if (meetsMinRole(user?.effective_role, minRole)) {
    return <>{children}</>
  }
  return (
    <LockedPanel
      message={m.role_guard_description({ minRole })}
    />
  )
}
