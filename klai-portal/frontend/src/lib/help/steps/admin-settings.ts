import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const adminSettingsSteps: HelpStep[] = [
  {
    id: 'admin-settings-general',
    title: () => m.help_admin_settings_general_title(),
    description: () => m.help_admin_settings_general_desc(),
  },
]
