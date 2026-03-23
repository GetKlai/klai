import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const appChatSteps: HelpStep[] = [
  {
    id: 'chat-page',
    title: () => m.help_chat_page_title(),
    description: () => m.help_chat_page_desc(),
  },
]
