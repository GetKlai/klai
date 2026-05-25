/**
 * Shared user avatar primitives. Mirrors the pattern that already lives in
 * routes/admin/groups/index.tsx so the visual treatment is identical between
 * users-list, profiles drill-in, and groups members.
 *
 * Avatar background colors are decorative tints (not semantic state), so raw
 * Tailwind classes are allowed per portal-patterns.md "Action icons in list
 * views (semantic colors - retained)" exception clause.
 */

const AVATAR_COLORS = [
  'bg-purple-100 text-purple-700',
  'bg-blue-100 text-blue-700',
  'bg-green-100 text-green-700',
  'bg-amber-100 text-amber-700',
  'bg-rose-100 text-rose-700',
]

export function avatarColor(uid: string): string {
  const hash = uid.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0)
  return AVATAR_COLORS[hash % AVATAR_COLORS.length]
}

export function userInitials(input: {
  first_name?: string | null
  last_name?: string | null
  email: string
}): string {
  if (input.first_name && input.last_name) {
    return `${input.first_name[0]}${input.last_name[0]}`.toUpperCase()
  }
  return input.email.slice(0, 2).toUpperCase()
}

/**
 * Standard "full name with email fallback" rendering used across the admin
 * surfaces (users table, profiles drill-in, groups detail). Lives next to
 * UserAvatar so the two stay in sync - same input shape, same fallback rules.
 */
export function displayName(input: {
  first_name?: string | null
  last_name?: string | null
  email: string
}): string {
  const full = `${input.first_name ?? ''} ${input.last_name ?? ''}`.trim()
  return full || input.email
}

interface UserAvatarProps {
  uid: string
  first_name?: string | null
  last_name?: string | null
  email: string
  size?: 'sm' | 'md'
}

export function UserAvatar({ uid, first_name, last_name, email, size = 'md' }: UserAvatarProps) {
  const dim = size === 'sm' ? 'h-7 w-7' : 'h-8 w-8'
  return (
    <div
      title={`${first_name ?? ''} ${last_name ?? ''}`.trim() || email}
      className={`${dim} rounded-full flex items-center justify-center text-xs font-medium ${avatarColor(uid)}`}
    >
      {userInitials({ first_name, last_name, email })}
    </div>
  )
}
