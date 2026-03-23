import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const appDocsSteps: HelpStep[] = [
  {
    id: 'docs-create',
    title: () => m.help_docs_create_title(),
    description: () => m.help_docs_create_desc(),
  },
  {
    id: 'docs-list',
    title: () => m.help_docs_list_title(),
    description: () => m.help_docs_list_desc(),
  },
]
