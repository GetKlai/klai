import {
  ListFrame,
  ListRow,
  ListRowActions,
  ListRowContent,
  ListRowDescription,
  ListRowTitle,
} from '@/components/ui/list'
import { formatDate, userDisplayName } from '../-users-helpers'
import type { AdminUser } from '../-users-types'
import { AccountTypeBadge, ProfileBadge, StatusBadge } from './UserBadges'
import type { ReactNode } from 'react'

interface Props {
  users: AdminUser[]
  onRowClick?: (row: AdminUser) => void
  renderActions: (row: AdminUser) => ReactNode
}

export function UsersTable({ users, onRowClick, renderActions }: Props) {
  return (
    <ListFrame data-help-id="admin-users-table">
      {users.map((user) => {
        const clickable = !!onRowClick
        return (
          <ListRow
            key={user.zitadel_user_id}
            interactive={clickable}
            onClick={clickable ? () => onRowClick(user) : undefined}
            className="grid items-center gap-x-5 gap-y-3 px-4 py-4 lg:grid-cols-[minmax(240px,1.4fr)_minmax(120px,0.75fr)_minmax(130px,0.85fr)_minmax(96px,0.65fr)_minmax(90px,0.55fr)_auto]"
          >
            <ListRowContent>
              <ListRowTitle>{userDisplayName(user)}</ListRowTitle>
              <ListRowDescription>{user.email}</ListRowDescription>
            </ListRowContent>
            <div>
              <ProfileBadge role={user.role} pending={user.invite_pending} />
            </div>
            <div>
              <AccountTypeBadge seat={user.seat_type} />
            </div>
            <div>
              <StatusBadge status={user.status} />
            </div>
            <div className="whitespace-nowrap text-sm text-gray-900">
              {formatDate(user.created_at)}
            </div>
            <ListRowActions
              className="self-center"
              onClick={(e) => e.stopPropagation()}
            >
              {renderActions(user)}
            </ListRowActions>
          </ListRow>
        )
      })}
    </ListFrame>
  )
}
