import { Badge } from '@/components/ui/badge'
import * as m from '@/paraglide/messages'
import { accountTypeLabel, profileLabel } from '../-users-helpers'
import type { SeatType, UserStatus } from '../-users-types'
import type { ProfileRole } from '@/lib/profiles'

export function ProfileBadge({ role, pending }: { role: ProfileRole; pending?: boolean }) {
  const variant = role === 'admin' ? 'accent' : 'secondary'
  if (pending) {
    return <Badge variant="warning">{profileLabel(role)}</Badge>
  }
  return <Badge variant={variant}>{profileLabel(role)}</Badge>
}

export function AccountTypeBadge({ seat }: { seat: SeatType }) {
  const variant: 'secondary' | 'accent' = seat === 'knowledge' ? 'accent' : 'secondary'
  return <Badge variant={variant}>{accountTypeLabel(seat)}</Badge>
}

export function StatusBadge({ status }: { status: UserStatus }) {
  switch (status) {
    case 'suspended':
      return <Badge variant="warning">{m.admin_users_status_suspended()}</Badge>
    case 'offboarded':
      return <Badge variant="destructive">{m.admin_users_status_offboarded()}</Badge>
    default:
      return <Badge variant="success">{m.admin_users_status_active()}</Badge>
  }
}
