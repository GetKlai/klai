// Shared types for admin API keys routes (SPEC-WIDGET-002)

export type AccessLevel = 'none' | 'read' | 'read_write'

export interface KbAccess {
  kb_id: number
  kb_name: string
  kb_slug: string
  access_level: AccessLevel
}

export interface ApiKeyResponse {
  id: number | string
  name: string
  description: string | null
  key_prefix: string
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

export interface ApiKeyDetailResponse extends ApiKeyResponse {
  kb_access: KbAccess[]
}

export interface CreateApiKeyRequest {
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

export interface CreateApiKeyResponse extends ApiKeyDetailResponse {
  api_key: string
}

export interface UpdateApiKeyRequest {
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
