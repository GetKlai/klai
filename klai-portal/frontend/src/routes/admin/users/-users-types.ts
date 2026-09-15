import type { ProfileRole } from '@/lib/profiles'

export type UserStatus = 'active' | 'suspended' | 'offboarded'

export type SeatType = 'chat' | 'knowledge'

export interface AdminUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: ProfileRole
  // SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0: per-user account type
  // (billing tier), derived server-side from ``role`` via ``suggest_seat``.
  seat_type: SeatType
  // Server-computed: the stored account type is not the tier that belongs to
  // ``role`` (a profile edit does not recompute seat_type). Drives the
  // non-blocking warning next to the account-type badge.
  seat_mismatch: boolean
  status: UserStatus
  preferred_language: 'nl' | 'en'
  created_at: string
  invite_pending: boolean
}

export interface AdminUsersResponse {
  users: AdminUser[]
}
