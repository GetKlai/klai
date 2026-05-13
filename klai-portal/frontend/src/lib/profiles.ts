/**
 * SPEC-PORTAL-PROFILES-001: Profile ladder constants shared across UI.
 * Mirror of backend app/core/profiles.py — keep in sync.
 */

export const PROFILE_LADDER = ['personal', 'company', 'kb_manager', 'group_manager', 'admin'] as const
export type ProfileRole = typeof PROFILE_LADDER[number]

export const PROFILE_RANK: Record<ProfileRole, number> = {
  personal: 0,
  company: 1,
  kb_manager: 2,
  group_manager: 3,
  admin: 4,
}

/**
 * Returns true when the given role meets or exceeds minRole on the ladder.
 * Falls back to false when either value is undefined/unknown.
 */
export function meetsMinRole(role: string | undefined, minRole: ProfileRole): boolean {
  if (!role) return false
  const rank = PROFILE_RANK[role as ProfileRole]
  if (rank === undefined) return false
  return rank >= PROFILE_RANK[minRole]
}
