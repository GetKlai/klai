// SPEC-PORTAL-PROFILES-001 P3.8: Convenience hook for effective capabilities from /api/me.
import { useCurrentUser } from '@/hooks/useCurrentUser'

export function useEffectiveCapabilities(): string[] {
  const { user } = useCurrentUser()
  return user?.effective_capabilities ?? []
}
