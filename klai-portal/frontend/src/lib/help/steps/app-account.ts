import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const appAccountSteps: HelpStep[] = [
  {
    id: 'account-profile',
    title: () => m.help_account_profile_title(),
    description: () => m.help_account_profile_desc(),
  },
  {
    id: 'account-2fa',
    title: () => m.help_account_2fa_title(),
    description: () => m.help_account_2fa_desc(),
  },
]
