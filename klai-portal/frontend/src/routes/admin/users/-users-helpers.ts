import type { ProfileRole } from '@/lib/profiles'
import { datetime, plural } from '@/paraglide/registry'
import { getLocale } from '@/paraglide/runtime'
import * as m from '@/paraglide/messages'
import type { AdminUser, SeatType } from './-users-types'

export function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

export function profileLabel(role: ProfileRole): string {
  const msgs = m as unknown as Record<string, (() => string) | undefined>
  const labelFn = msgs[`profile_${role}_label`]
  return labelFn ? labelFn() : role
}

export function accountTypeLabel(seat: SeatType): string {
  if (seat === 'knowledge') return m.admin_users_account_knowledge_label()
  return m.admin_users_account_chat_label()
}

export function userDisplayName(user: AdminUser): string {
  return `${user.first_name} ${user.last_name}`.trim() || user.email
}

export function filterUsers(users: AdminUser[], searchQuery: string): AdminUser[] {
  const query = searchQuery.trim().toLowerCase()
  if (!query) return users
  return users.filter((user) => {
    const fullName = `${user.first_name} ${user.last_name}`.toLowerCase()
    return fullName.includes(query) || user.email.toLowerCase().includes(query)
  })
}

export function userCountLabel(count: number): string {
  return plural(getLocale(), count) === 'one'
    ? m.admin_users_count_one()
    : m.admin_users_count_other({ count: String(count) })
}
