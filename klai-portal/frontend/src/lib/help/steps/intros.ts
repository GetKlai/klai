import type { HelpPageIntro } from '../types'
import * as m from '@/paraglide/messages'

const home: HelpPageIntro = {
  title: () => m.help_home_intro_title(),
  description: () => m.help_home_intro_desc(),
}
const chat: HelpPageIntro = {
  title: () => m.help_chat_intro_title(),
  description: () => m.help_chat_intro_desc(),
}
const transcribe: HelpPageIntro = {
  title: () => m.help_transcribe_intro_title(),
  description: () => m.help_transcribe_intro_desc(),
}
const docs: HelpPageIntro = {
  title: () => m.help_docs_intro_title(),
  description: () => m.help_docs_intro_desc(),
}
const account: HelpPageIntro = {
  title: () => m.help_account_intro_title(),
  description: () => m.help_account_intro_desc(),
}
const adminUsers: HelpPageIntro = {
  title: () => m.help_admin_users_intro_title(),
  description: () => m.help_admin_users_intro_desc(),
}
const adminSettings: HelpPageIntro = {
  title: () => m.help_admin_settings_intro_title(),
  description: () => m.help_admin_settings_intro_desc(),
}
const adminBilling: HelpPageIntro = {
  title: () => m.help_admin_billing_intro_title(),
  description: () => m.help_admin_billing_intro_desc(),
}

export const routeIntros: Record<string, HelpPageIntro> = {
  '/app': home,
  '/app/': home,
  '/app/chat': chat,
  '/app/transcribe': transcribe,
  '/app/transcribe/': transcribe,
  '/app/docs': docs,
  '/app/docs/': docs,
  '/app/account': account,
  '/admin/users': adminUsers,
  '/admin/users/': adminUsers,
  '/admin/settings': adminSettings,
  '/admin/billing': adminBilling,
}
