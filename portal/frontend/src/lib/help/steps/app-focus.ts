import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const appFocusSteps: HelpStep[] = [
  {
    id: 'focus-add',
    title: () => m.help_focus_add_title(),
    description: () => m.help_focus_add_desc(),
  },
  {
    id: 'focus-list',
    title: () => m.help_focus_list_title(),
    description: () => m.help_focus_list_desc(),
  },
]
