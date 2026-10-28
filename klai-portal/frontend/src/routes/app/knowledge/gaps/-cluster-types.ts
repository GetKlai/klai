// Shapes and labels shared by the weekly cluster list, the cluster detail and the
// proposal section (backend: /api/app/gaps/clusters). A cluster is the unit the
// owner works on: questions from the stored chat signals that point at the same
// missing or incomplete knowledge.
import * as m from '@/paraglide/messages'

export type ClusterKind = 'no_article' | 'article_misses_part' | 'variant_unclear' | 'contact_wish' | 'weak_match'
export type ClusterDecision = 'rejected' | 'out_of_scope'

export interface ClusterArticle {
  title: string
  url: string | null
  score: number
}

export interface ClusterRow {
  cluster_key: string
  product: string | null
  topic: string
  count: number
  widget_count: number
  internal_count: number
  kind: ClusterKind
  kinds: Record<string, number>
  // null for a cluster that only has internal-chat signals (no stored text).
  question: string | null
  questions: string[]
  article: ClusterArticle | null
  articles: ClusterArticle[]
  open_parts: string[]
  language: string | null
  last_seen: string
  decision: ClusterDecision | 'none'
  decided_by: string | null
  decided_at: string | null
  has_proposal: boolean
}

export interface ClustersResponse {
  days: number
  clusters: ClusterRow[]
}

export interface ClusterProposal {
  id: number
  cluster_key: string
  missing: string
  where_kind: 'existing' | 'new'
  where_title: string
  where_url: string | null
  where_section: string | null
  example_text: string
  open_facts: string[]
  language: string | null
  model: string
  generated_at: string
  decision: ClusterDecision | 'none'
  decided_by: string | null
  decided_at: string | null
}

export function clustersPath(days: number, includeDecided: boolean): string {
  return `/api/app/gaps/clusters?days=${days}&include_decided=${includeDecided}`
}

export const clusterPath = (clusterKey: string) => `/api/app/gaps/clusters/${encodeURIComponent(clusterKey)}`

export function clusterKindLabel(kind: ClusterKind): string {
  switch (kind) {
    case 'no_article':
      return m.gaps_cluster_kind_no_article()
    case 'article_misses_part':
      return m.gaps_cluster_kind_article_misses_part()
    case 'variant_unclear':
      return m.gaps_cluster_kind_variant_unclear()
    case 'contact_wish':
      return m.gaps_cluster_kind_contact_wish()
    case 'weak_match':
      return m.gaps_cluster_kind_weak_match()
  }
}

export function clusterTitle(cluster: Pick<ClusterRow, 'product' | 'topic'>): string {
  return cluster.product ? `${cluster.product} · ${cluster.topic}` : cluster.topic
}

export function decisionLabel(decision: ClusterDecision): string {
  return decision === 'rejected' ? m.gaps_decision_label_rejected() : m.gaps_decision_label_out_of_scope()
}

// Only http(s) becomes a link: article and proposal URLs come from stored or
// generated text, and a javascript: href would run in the portal origin.
export const isWebUrl = (url: string | null): url is string => url != null && /^https?:\/\//i.test(url)
