import {
  ListFrame,
  ListHeader,
  ListRow,
  ListRowActions,
  ListRowContent,
  ListRowDescription,
  ListRowTitle,
} from '@/components/ui/list'
import * as m from '@/paraglide/messages'
import { formatDate, userDisplayName } from '../-users-helpers'
import type { AdminUser } from '../-users-types'
import { AccountTypeBadge, ProfileBadge, StatusBadge } from './UserBadges'
import type { ReactNode } from 'react'

interface Props {
  users: AdminUser[]
  onRowClick?: (row: AdminUser) => void
  renderActions: (row: AdminUser) => ReactNode
}

const userListGrid =
  'lg:grid-cols-[minmax(260px,1.5fr)_minmax(120px,0.72fr)_minmax(130px,0.8fr)_minmax(96px,0.6fr)_minmax(90px,0.55fr)_auto]'

export function UsersTable({ users, onRowClick, renderActions }: Props) {
  return (
    <ListFrame data-help-id="admin-users-table">
      <ListHeader className={`hidden gap-x-5 ${userListGrid} lg:grid`}>
        <span>{m.admin_users_col_name()}</span>
        <span>{m.admin_users_field_profile()}</span>
        <span>{m.admin_users_col_account_type()}</span>
        <span>{m.admin_users_col_status()}</span>
        <span>{m.admin_users_col_invited()}</span>
        <span className="text-right">{m.admin_users_col_actions()}</span>
      </ListHeader>

      {users.map((user) => {
        const clickable = !!onRowClick
        return (
          <ListRow
            key={user.zitadel_user_id}
            interactive={clickable}
            onClick={clickable ? () => onRowClick(user) : undefined}
            className={`grid items-center gap-x-5 gap-y-3 px-4 py-4 ${userListGrid}`}
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
