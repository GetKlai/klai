import type { HelpStep } from '../types'
import * as m from '@/paraglide/messages'

export const appTranscribeSteps: HelpStep[] = [
  {
    id: 'transcribe-add',
    title: () => m.help_transcribe_add_title(),
    description: () => m.help_transcribe_add_desc(),
  },
  {
    id: 'transcribe-list',
    title: () => m.help_transcribe_list_title(),
    description: () => m.help_transcribe_list_desc(),
  },
  {
    id: 'transcribe-copy',
    title: () => m.help_transcribe_copy_title(),
    description: () => m.help_transcribe_copy_desc(),
  },
  {
    id: 'transcribe-download',
    title: () => m.help_transcribe_download_title(),
    description: () => m.help_transcribe_download_desc(),
  },
]
