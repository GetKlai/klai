import { AlertTriangle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
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

export function AccountTypeBadge({
  seat,
  mismatch,
}: {
  seat: SeatType
  mismatch?: boolean
}) {
  const variant: 'secondary' | 'accent' = seat === 'knowledge' ? 'accent' : 'secondary'
  const hint = m.admin_users_account_mismatch_hint()
  return (
    <div className="flex items-center gap-1.5">
      <Badge variant={variant}>{accountTypeLabel(seat)}</Badge>
      {mismatch && (
        <Tooltip label={hint}>
          <button
            type="button"
            aria-label={hint}
            className="flex items-center rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-warning-text)]"
            onClick={(e) => e.stopPropagation()}
          >
            <AlertTriangle className="h-3.5 w-3.5 text-[var(--color-warning-text)]" aria-hidden="true" />
          </button>
        </Tooltip>
      )}
    </div>
  )
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
