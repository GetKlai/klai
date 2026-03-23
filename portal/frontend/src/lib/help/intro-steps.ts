import type { HelpPageIntro } from './types'
import * as m from '@/paraglide/messages'

export const genericIntro: HelpPageIntro = {
  title: () => m.help_intro_title(),
  description: () => m.help_intro_desc(),
}
