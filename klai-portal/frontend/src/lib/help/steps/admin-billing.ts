import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const adminBillingSteps: HelpStep[] = [
  {
    id: 'admin-billing-overview',
    title: () => m.help_admin_billing_overview_title(),
    description: () => m.help_admin_billing_overview_desc(),
  },
]
