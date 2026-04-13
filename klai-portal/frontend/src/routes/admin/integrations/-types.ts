// Shared types for admin integrations routes

export type AccessLevel = 'none' | 'read' | 'read_write'

export interface KbAccess {
  kb_id: number
  kb_name: string
  kb_slug: string
  access_level: AccessLevel
}

export interface IntegrationResponse {
  id: number
  name: string
  description: string | null
  key_prefix: string
  active: boolean
  permissions: {
    chat: boolean
    feedback: boolean
    knowledge_append: boolean
  }
  rate_limit_rpm: number
  kb_access_count: number
  last_used_at: string | null
  created_at: string
  created_by: string
}

export interface IntegrationDetailResponse extends IntegrationResponse {
  kb_access: KbAccess[]
}

export interface CreateIntegrationRequest {
  name: string
  description: string | null
  permissions: {
    chat: boolean
    feedback: boolean
    knowledge_append: boolean
  }
  rate_limit_rpm: number
  kb_access: { kb_id: number; access_level: AccessLevel }[]
}

export interface CreateIntegrationResponse extends IntegrationDetailResponse {
  api_key: string
}

export interface UpdateIntegrationRequest {
  name?: string
  description?: string | null
  permissions?: {
    chat: boolean
    feedback: boolean
    knowledge_append: boolean
  }
  rate_limit_rpm?: number
  kb_access?: { kb_id: number; access_level: AccessLevel }[]
}

export interface OrgKnowledgeBase {
  id: number
  name: string
  slug: string
  owner_type: string
}
