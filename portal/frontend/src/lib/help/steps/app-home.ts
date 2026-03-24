import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const appHomeSteps: HelpStep[] = [
  {
    id: 'home-greeting',
    title: () => m.help_home_greeting_title(),
    description: () => m.help_home_greeting_desc(),
  },
  {
    id: 'home-tool-chat',
    title: () => m.help_home_chat_title(),
    description: () => m.help_home_chat_desc(),
  },
  {
    id: 'home-tool-transcribe',
    title: () => m.help_home_transcribe_title(),
    description: () => m.help_home_transcribe_desc(),
  },
  {
    id: 'home-tool-focus',
    title: () => m.help_home_focus_title(),
    description: () => m.help_home_focus_desc(),
  },
  {
    id: 'home-tool-docs',
    title: () => m.help_home_docs_title(),
    description: () => m.help_home_docs_desc(),
  },
  {
    id: 'home-tool-knowledge',
    title: () => m.help_home_knowledge_title(),
    description: () => m.help_home_knowledge_desc(),
  },
]
