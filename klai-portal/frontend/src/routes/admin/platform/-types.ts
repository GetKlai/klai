// SPEC-PLATFORM-ADMIN-001 - cross-tenant console types.
// Mirror 1:1 of the Pydantic responses in app/api/admin/platform.py.

export interface PlatformStats {
  total_users: number
  new_users_this_month: number
  total_orgs: number
  active_subscriptions: number
  total_bots: number
  new_bots_today: number
  total_kbs: number
  total_templates: number
  mrr_cents: number
  arr_cents: number
}

export interface PlatformUser {
  zitadel_user_id: string
  email: string | null
  display_name: string | null
  role: string
  is_admin: boolean
  status: string
  org_id: number
  org_name: string
  org_slug: string
  org_plan: string
  org_onboarded: boolean
  created_at: string
}

export interface PlatformOrg {
  id: number
  name: string
  slug: string
  plan: string
  billing_status: string
  billing_cycle: string
  seats: number
  provisioning_status: string
  user_count: number
  bot_count: number
  kb_count: number
  created_at: string
}

export interface PlatformKB {
  id: number
  name: string
  slug: string
  org_id: number
  org_name: string
  org_slug: string
  owner_type: string
  visibility: string
  created_at: string
}

export interface PlatformTemplate {
  id: number
  name: string
  slug: string
  org_id: number
  org_name: string
  org_slug: string
  scope: string
  created_by: string
  created_by_name: string | null
  is_active: boolean
  created_at: string
}

export interface PlatformBot {
  id: string
  name: string
  widget_id: string
  org_id: number
  org_name: string
  org_slug: string
  kb_count: number
  created_at: string
}

export interface PlatformChatError {
  id: number
  org_id: number
  org_name: string | null
  event_type: string
  detail: string | null
  created_at: string
}

export interface PlatformFeedbackSubmission {
  id: number
  org_id: number | null
  org_name: string | null
  org_slug: string | null
  user_id: string | null
  event_type: string
  raw_text: string | null
  feedback_type: string | null
  severity: string | null
  page_url: string | null
  route_id: string | null
  locale: string | null
  viewport: string | null
  created_at: string
}

export interface PlatformOrgDetail {
  org: PlatformOrg
  users: PlatformUser[]
  bots: PlatformBot[]
  knowledge_bases: PlatformKB[]
  templates: PlatformTemplate[]
}

export interface CreateTenantPayload {
  company_name: string
  owner_email: string
  owner_first_name: string
  owner_last_name: string
  preferred_language: 'nl' | 'en'
}

export interface CreateTenantResult {
  org_id: number
  slug: string
  owner_user_id: string
  message: string
}

export type PlatformTab =
  | 'users'
  | 'organizations'
  | 'knowledge-bases'
  | 'templates'
  | 'subscriptions'
  | 'bots'
  | 'feedback'
  | 'chat-errors'
  | 'status'
