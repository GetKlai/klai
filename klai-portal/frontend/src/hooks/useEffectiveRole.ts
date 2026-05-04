// SPEC-PORTAL-PROFILES-001 P3.8: Convenience hook for effective role from /api/me.
import { useCurrentUser } from '@/hooks/useCurrentUser'

export function useEffectiveRole(): string | undefined {
  const { user } = useCurrentUser()
  return user?.effective_role
}
