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
  total_feedback_count: number
  new_feedback_count: number
  unread_message_count: number
  chat_error_count: number
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
  deletion_status: string | null
  deletion_failure_reason: Record<string, unknown> | null
  deletion_last_attempted_step: string | null
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
  platform_unlocked_features: string[]
  billing_status: string
  billing_cycle: string
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

export interface PlatformFeedbackDuplicateCandidate {
  item_id: number
  confidence: number | null
  reason: string | null
  title: string | null
  kind: string | null
  status: string | null
  area: string | null
}

export interface PlatformFeedbackTriageSuggestion {
  classification: string | null
  summary: string | null
  suggested_area: string | null
  suggested_severity: string | null
  suggested_action: string | null
  duplicate_candidates: PlatformFeedbackDuplicateCandidate[]
  model: string | null
  created_at: string | null
}

export interface PlatformFeedbackSubmission {
  id: number
  org_id: number | null
  org_name: string | null
  org_slug: string | null
  user_id: string | null
  user_email: string | null
  user_display_name: string | null
  event_type: string
  status: string
  raw_text: string | null
  feedback_type: string | null
  severity: string | null
  page_url: string | null
  route_id: string | null
  locale: string | null
  viewport: string | null
  created_at: string
  triage_suggestion: PlatformFeedbackTriageSuggestion | null
  linked_item_id: number | null
  linked_item_title: string | null
  linked_item_status: string | null
}

export interface PlatformFeedbackItem {
  id: number
  kind: string
  title: string
  summary: string | null
  status: string
  area: string | null
  priority_score: number
  org_count: number
  user_count: number
  shipped_at: string | null
  resolution_summary: string | null
  resolved_at: string | null
  resolved_by: string | null
  notification_state: string | null
  reporter_orgs: PlatformFeedbackReporterOrg[]
  created_at: string
  updated_at: string
}

export interface PlatformFeedbackReporterOrg {
  org_id: number | null
  org_name: string | null
  org_slug: string | null
  user_count: number
}

export interface PlatformFeedbackLinkedSubmission extends PlatformFeedbackSubmission {
  link_type: string
  linked_at: string
}

export interface PlatformFeedbackItemDetail {
  item: PlatformFeedbackItem
  submissions: PlatformFeedbackLinkedSubmission[]
}

export interface PlatformFeedbackActionResult {
  ok: boolean
  submission_id: number
  status: string
  item_id: number | null
}

export interface PlatformFeedbackNotification {
  id: number
  item_id: number
  submission_id: number | null
  org_id: number | null
  user_id: string | null
  recipient_email: string | null
  channel: string
  status: string
  subject: string | null
  body: string
  sent_at: string | null
  read_at: string | null
  created_at: string
}

export interface PlatformFeedbackResolveResult {
  item: PlatformFeedbackItem
  notifications: PlatformFeedbackNotification[]
  recipient_count: number
}

export interface PlatformMessageThread {
  id: number
  org_id: number
  org_name: string | null
  org_slug: string | null
  subject: string
  origin_type: string
  feedback_submission_id: number | null
  feedback_item_id: number | null
  recipient_count: number
  latest_message_body: string
  latest_message_sender_type: string
  latest_message_at: string
  latest_user_message_at: string | null
  latest_admin_message_at: string | null
  unread_for_admin: boolean
  created_by: string
  created_at: string
}

export interface PlatformMessageRecipient {
  user_id: string
  email: string | null
  display_name: string | null
  last_read_at: string | null
}

export interface PlatformMessage {
  id: number
  sender_type: string
  sender_user_id: string | null
  sender_display_name: string | null
  body: string
  created_at: string
}

export interface PlatformMessageThreadDetail {
  thread: PlatformMessageThread
  recipients: PlatformMessageRecipient[]
  messages: PlatformMessage[]
}

export interface PlatformOrgDetail {
  org: PlatformOrg
  users: PlatformUser[]
  bots: PlatformBot[]
  knowledge_bases: PlatformKB[]
  templates: PlatformTemplate[]
}

export interface PlatformUnlockFeature {
  key: string
  enabled: boolean
  requires_profile: string | null
}

export interface PlatformUnlocksResponse {
  slug: string
  platform_unlocked_features: string[]
  features: PlatformUnlockFeature[]
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
  | 'messages'
  | 'knowledge-bases'
  | 'templates'
  | 'subscriptions'
  | 'bots'
  | 'feedback'
  | 'chat-errors'
  | 'status'
  | 'subdomains'

export interface PlatformSubdomainItem {
  subdomain: string
  url: string
  label: string
  description: string
  category: 'klai_service' | 'tooling' | 'marketing' | 'tenant'
  host: 'core-01' | 'public-01' | 'gpu-01' | 'external'
  owner: string
  status: 'up' | 'auth_required' | 'client_error' | 'server_error' | 'unreachable' | 'not_probed'
  status_code: number | null
}
