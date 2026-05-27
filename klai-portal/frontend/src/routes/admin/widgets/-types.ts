// Shared types for admin widgets routes (SPEC-WIDGET-002)

export type AccessLevel = 'none' | 'read' | 'read_write'

export interface WidgetConfig {
  allowed_origins: string[]
  title: string
  welcome_message: string
  system_prompt: string
  css_variables: Record<string, string>
  conversation_starters: string[]
  hide_disclaimer: boolean
  template_slug: string | null
  primary_color: string
  theme: 'light' | 'dark'
  show_sources: boolean
  show_meta: boolean
  collect_user_info: boolean
  page_context_enabled: boolean
  widget_position: 'left' | 'right'
  integrations?: WidgetIntegrations
}

export type HubSpotIntegrationStatusValue =
  | 'not_configured'
  | 'not_connected'
  | 'connected'
  | 'disconnected'
  | 'error'

export interface HubSpotWidgetIntegration {
  status: Exclude<HubSpotIntegrationStatusValue, 'not_configured'>
  portal_id: string | null
  channel_id: string | null
  channel_account_id: string | null
  inbox_id: string | null
  help_desk_url: string | null
  last_connected_at: string | null
  last_disconnected_at: string | null
  last_rebuilt_at: string | null
  last_tested_at: string | null
  last_test_thread_id: string | null
  last_error: string | null
}

export interface WidgetIntegrations {
  hubspot: HubSpotWidgetIntegration
}

export interface HubSpotIntegrationStatus {
  configured: boolean
  status: HubSpotIntegrationStatusValue
  portal_id: string | null
  channel_id: string | null
  channel_account_id: string | null
  inbox_id: string | null
  help_desk_url: string | null
  last_connected_at: string | null
  last_disconnected_at: string | null
  last_rebuilt_at: string | null
  last_tested_at: string | null
  last_test_thread_id: string | null
  last_error: string | null
}

export interface KbAccess {
  kb_id: number
  kb_name: string
  kb_slug: string
}

export interface WidgetResponse {
  id: string
  name: string
  description: string | null
  widget_id: string
  widget_config: WidgetConfig
  public_share_enabled: boolean
  // REQ-2 (Finding B-2): allow_any_origin bypasses the allowed_origins gate.
  // @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
  allow_any_origin: boolean
  rate_limit_rpm: number
  kb_access_count: number
  last_used_at: string | null
  created_at: string
  created_by: string
}

export interface WidgetDetailResponse extends WidgetResponse {
  kb_access: KbAccess[]
}

export interface CreateWidgetRequest {
  name: string
  description: string | null
  kb_ids: number[]
  rate_limit_rpm: number
  widget_config: WidgetConfig | null
  public_share_enabled?: boolean
  allow_any_origin?: boolean
}

export interface UpdateWidgetRequest {
  name?: string
  description?: string | null
  kb_ids?: number[]
  rate_limit_rpm?: number
  widget_config?: WidgetConfig
  public_share_enabled?: boolean
  allow_any_origin?: boolean
}

export interface OrgKnowledgeBase {
  id: number
  name: string
  slug: string
  owner_type: string
}

// SPEC-WIDGET-ACTIVITY-001 - audit-trail types.
export type StatsPeriod = '7d' | '30d' | 'all'

export interface ConversationListItem {
  id: number
  started_at: string
  last_message_at: string
  message_count: number
  first_user_query: string | null
  language_detected: string | null
}

export interface MessageSourceItem {
  label: string
  title: string
  url: string
}

export interface WidgetMessageItem {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources: MessageSourceItem[] | null
  created_at: string
  sequence: number
}

export interface ConversationDetail extends ConversationListItem {
  messages: WidgetMessageItem[]
}

export interface TopQuery {
  query: string
  count: number
}

export interface WidgetStats {
  period: StatsPeriod
  total_conversations: number
  total_messages: number
  avg_messages_per_conversation: number
  top_queries: TopQuery[]
  hourly_activity: number[]
}
