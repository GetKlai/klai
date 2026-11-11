// Shapes and labels shared by the Knowledge gaps list, the group detail and the
// proposal section (backend: /api/app/gaps/clusters). A cluster is the unit the
// owner works on: questions from the stored chat signals that point at the same
// missing or incomplete knowledge.
import { ApiError } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

export type ClusterKind =
  | 'no_article'
  | 'article_misses_part'
  | 'variant_unclear'
  | 'contact_wish'
  | 'weak_match'
  | 'reported_review'
  | 'reported_judge'
export type ClusterDecision = 'rejected' | 'out_of_scope'
// Which kinds the list shows: the article gaps by default, the answers given from a weak match, the visitors who asked for a person, or all.
export type GapView = 'article_gaps' | 'weak_matches' | 'contact_requests' | 'all'

export interface ClusterArticle {
  title: string | null
  url: string | null
  score: number | null
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
  // Who reported the group through a filed gap row, next to (or instead of) chat turns.
  reported_by: Array<'review' | 'judge'>
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
  // false for internal-only clusters and clusters without topic words: the
  // proposal and decision endpoints answer 409 for them.
  decidable: boolean
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

export function clustersPath(days: number, includeDecided: boolean, show: GapView): string {
  return `/api/app/gaps/clusters?days=${days}&include_decided=${includeDecided}&show=${show}`
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
    case 'reported_review':
      return m.gaps_cluster_kind_reported_review()
    case 'reported_judge':
      return m.gaps_cluster_kind_reported_judge()
  }
}

// A group that keeps its chat kind says so when a reviewer or the quality check also reported it.
export function alsoReportedLabel(cluster: Pick<ClusterRow, 'kind' | 'reported_by'>): string | null {
  if (cluster.kind === 'reported_review' || cluster.kind === 'reported_judge' || cluster.reported_by.length === 0) return null
  const who = cluster.reported_by.map((by) => (by === 'review' ? m.gaps_reported_by_review() : m.gaps_reported_by_judge()))
  return m.gaps_clusters_also_reported({ who: who.join(', ') })
}

export function clusterTitle(cluster: Pick<ClusterRow, 'product' | 'topic'>): string {
  return cluster.product ? `${cluster.product} · ${cluster.topic}` : cluster.topic
}

export function notDecidableReason(clusterKey: string): string {
  return clusterKey.startsWith('internal|')
    ? m.gaps_clusters_not_decidable_internal()
    : m.gaps_clusters_not_decidable_no_topic()
}

// The proposal endpoints answer every refusal with a stable error_code (backend
// app_gap_clusters.py); each one maps to what the owner can do about it. The raw
// detail string is never shown.
export function proposalErrorMessage(err: unknown, fallback: string): string {
  let code: unknown
  if (err instanceof ApiError) {
    try {
      code = (JSON.parse(err.detail) as { error_code?: unknown }).error_code
    } catch {
      // not a structured detail
    }
  }
  switch (code) {
    case 'telemetry_below_full':
      return m.gaps_error_telemetry_below_full()
    case 'proposal_failed_twice':
      return m.gaps_error_proposal_failed_twice()
    case 'no_questions':
      return m.gaps_error_no_questions()
    case 'proposal_cap_reached':
      return m.gaps_error_proposal_cap_reached()
    case 'proposal_upstream_failed':
      return m.gaps_error_proposal_upstream_failed()
    case 'proposal_generating':
      return m.gaps_error_proposal_generating()
    case 'already_decided':
      return m.gaps_error_already_decided()
    case 'not_decidable':
      return m.gaps_error_not_decidable()
    default:
      return fallback
  }
}

export function decisionLabel(decision: ClusterDecision): string {
  return decision === 'rejected' ? m.gaps_decision_label_rejected() : m.gaps_decision_label_out_of_scope()
}

// Only http(s) becomes a link: article and proposal URLs come from stored or
// generated text, and a javascript: href would run in the portal origin.
// An article can come without a title; its url then stands in as the link text.
export const articleLabel = (article: ClusterArticle): string | null => article.title ?? article.url

export const isWebUrl = (url: string | null): url is string => url != null && /^https?:\/\//i.test(url)
