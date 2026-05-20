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
  widget_position: 'left' | 'right'
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
}

export interface UpdateWidgetRequest {
  name?: string
  description?: string | null
  kb_ids?: number[]
  rate_limit_rpm?: number
  widget_config?: WidgetConfig
}

export interface OrgKnowledgeBase {
  id: number
  name: string
  slug: string
  owner_type: string
}
