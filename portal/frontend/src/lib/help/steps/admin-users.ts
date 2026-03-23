import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const adminUsersSteps: HelpStep[] = [
  {
    id: 'admin-users-invite',
    title: () => m.help_admin_users_invite_title(),
    description: () => m.help_admin_users_invite_desc(),
  },
  {
    id: 'admin-users-table',
    title: () => m.help_admin_users_table_title(),
    description: () => m.help_admin_users_table_desc(),
  },
]
