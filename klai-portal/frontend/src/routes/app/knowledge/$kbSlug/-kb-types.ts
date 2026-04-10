// Shared types for knowledge base detail routes

export type KBTab = 'overview' | 'connectors' | 'members' | 'items' | 'taxonomy' | 'settings'

export interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  created_by: string
  visibility: string
  docs_enabled: boolean
  gitea_repo_slug: string | null
  owner_type: string
  default_org_role: string | null
}

export interface ConnectorSummary {
  id: string
  name: string
  connector_type: string
  config: Record<string, unknown>
  schedule: string | null
  is_enabled: boolean
  last_sync_status: string | null
  last_sync_at: string | null
  allowed_assertion_modes: string[] | null
}

export interface KBStats {
  docs_count: number | null
  connector_count: number
  connectors: ConnectorSummary[]
  volume: number | null
  usage_last_30d: number | null
  org_gap_count_7d: number | null
  // Volume breakdown
  source_page_count: number | null
  vector_chunk_count: number | null
  graph_entity_count: number | null
  graph_edge_count: number | null
}

export interface UserMember {
  id: number
  user_id: string
  display_name: string | null
  email: string | null
  role: string
  granted_at: string
  granted_by: string
}

export interface GroupMember {
  id: number
  group_id: number
  group_name: string
  role: string
  granted_at: string
  granted_by: string
}

export interface MembersResponse {
  users: UserMember[]
  groups: GroupMember[]
}

export interface TaxonomyNode {
  id: number
  kb_id: number
  parent_id: number | null
  name: string
  slug: string
  description?: string | null
  doc_count: number
  sort_order: number
  created_at: string
  created_by: string
}

export interface TaxonomyProposal {
  id: number
  kb_id: number
  proposal_type: string
  status: string
  title: string
  payload: Record<string, unknown>
  confidence_score: number | null
  created_at: string
  reviewed_at: string | null
  reviewed_by: string | null
  rejection_reason: string | null
}

export interface PersonalItem {
  id: string
  path: string
  assertion_mode: string | null
  tags: string[]
  created_at: string
}

export interface PersonalItemsResponse {
  items: PersonalItem[]
  total: number
  limit: number
  offset: number
}

export interface TaxonomyCoverageNode {
  taxonomy_node_id: number
  taxonomy_node_name: string
  description?: string | null
  chunk_count: number
  gap_count: number
  health: 'healthy' | 'attention_needed' | 'empty'
}

export interface TaxonomyCoverage {
  nodes: TaxonomyCoverageNode[]
  total_chunks: number
  untagged_count: number
  untagged_percentage: number
}

export interface TopTagEntry {
  tag: string
  count: number
}

export interface TopTagsResponse {
  tags: TopTagEntry[]
  total_chunks_sampled: number
}

export interface GitHubConfig {
  installation_id: string
  repo_owner: string
  repo_name: string
  branch: string
  path_filter: string
}

export interface WebCrawlerConfig {
  base_url: string
  path_prefix: string
  max_pages: string
  content_selector: string
}
