import type { HelpStep } from '../types'
export { routeIntros } from './intros'
import { appHomeSteps } from './app-home'
import { appChatSteps } from './app-chat'
import { appTranscribeSteps } from './app-transcribe'
import { appDocsSteps } from './app-docs'
import { appAccountSteps } from './app-account'
import { adminUsersSteps } from './admin-users'
import { adminSettingsSteps } from './admin-settings'
import { adminBillingSteps } from './admin-billing'

/**
 * Registry mapping route pathnames to their help step definitions.
 * Trailing slashes are normalized: both `/app` and `/app/` resolve to the same steps.
 */
export const routeSteps: Record<string, HelpStep[]> = {
  '/app': appHomeSteps,
  '/app/': appHomeSteps,
  '/app/chat': appChatSteps,
  '/app/transcribe': appTranscribeSteps,
  '/app/transcribe/': appTranscribeSteps,
  '/app/docs': appDocsSteps,
  '/app/docs/': appDocsSteps,
  '/app/account': appAccountSteps,
  '/admin/users': adminUsersSteps,
  '/admin/users/': adminUsersSteps,
  '/admin/settings': adminSettingsSteps,
  '/admin/billing': adminBillingSteps,
}
