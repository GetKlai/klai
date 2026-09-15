// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: data layer for the knowledge activity
// screen. The list is server-paginated by cursor, so the filter set is the
// request URL; both hooks stay gated on the kb.activity capability so a screen
// can never fetch without it.

import {
  useMutation,
  useQuery,
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryKey,
} from '@tanstack/react-query'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import type { ConversationMessage, ConversationQuality } from './types'

export type ConversationBand = 'high' | 'medium' | 'low' | 'unknown'
export type ConversationRating = 'thumbsUp' | 'thumbsDown' | 'none'
export type ConversationReviewStatus = 'unreviewed' | 'reviewed'

/** One row of `GET /api/app/activity/conversations`. */
export interface ConversationListItem {
  id: number
  widget_id: string
  widget_name: string
  channel: 'webchat' | 'librechat'
  started_at: string
  last_message_at: string
  message_count: number
  first_user_query: string | null
  language: string | null
  worst_band: ConversationBand | null
  judge: {
    outcome: string
    failure_category: string | null
    confidence: string
  } | null
  ratings: { up: number; down: number }
  review: {
    status: ConversationReviewStatus
    worst_verdict: string | null
    causes: string[]
  }
  open_gap_count: number
}

export interface ConversationListResponse {
  items: ConversationListItem[]
  next_cursor: string | null
}

/**
 * One filter set for the list. The contract fields the backend accepts more
 * than once (`judge_outcome`, `failure_category`, `cause`, `band`) also accept
 * an array here; `cursor` is the pagination position, not part of the key.
 */
export interface ActivityConversationQuery {
  days?: number
  channel?: 'webchat' | 'librechat'
  widgetId?: string
  language?: string
  judgeOutcome?: string | string[]
  failureCategory?: string | string[]
  reviewStatus?: ConversationReviewStatus
  cause?: string | string[]
  band?: ConversationBand | ConversationBand[]
  rating?: ConversationRating
  queue?: boolean
  sort?: 'newest' | 'worst'
  cursor?: string | null
  limit?: number
}

export function activityConversationsPath(query: ActivityConversationQuery): string {
  const params = new URLSearchParams({
    limit: String(query.limit ?? 20),
    days: String(query.days ?? 7),
    channel: query.channel ?? 'webchat',
    sort: query.sort ?? 'newest',
  })
  // Repeatable contract fields: one `append` per value, empty values dropped.
  const append = (key: string, value: string | string[] | undefined) => {
    for (const single of Array.isArray(value) ? value : value ? [value] : []) {
      params.append(key, single)
    }
  }
  if (query.widgetId) params.set('widget_id', query.widgetId)
  if (query.language) params.set('language', query.language)
  append('judge_outcome', query.judgeOutcome)
  append('failure_category', query.failureCategory)
  if (query.reviewStatus) params.set('review_status', query.reviewStatus)
  append('cause', query.cause)
  append('band', query.band)
  if (query.rating) params.set('rating', query.rating)
  if (query.queue !== undefined) params.set('queue', String(query.queue))
  if (query.cursor) params.set('cursor', query.cursor)
  return `/api/app/activity/conversations?${params.toString()}`
}

export function fetchActivityConversations(
  query: ActivityConversationQuery,
): Promise<ConversationListResponse> {
  return apiFetch<ConversationListResponse>(activityConversationsPath(query))
}

/** Capability gate shared by both activity hooks (SPEC §4.3 access model). */
function useActivityAccess(): boolean {
  const auth = useAuth()
  const { user } = useCurrentUser()
  return auth.isAuthenticated && user?.hasCapability('kb.activity') === true
}

export function useActivityConversations(query: ActivityConversationQuery) {
  const enabled = useActivityAccess()
  const { cursor, limit, ...filters } = query
  return useInfiniteQuery<
    ConversationListResponse,
    Error,
    InfiniteData<ConversationListResponse, string | null>,
    QueryKey,
    string | null
  >({
    queryKey: ['activity', 'conversations', filters],
    queryFn: async ({ pageParam }) => {
      try {
        return await fetchActivityConversations({
          ...filters,
          limit,
          cursor: pageParam ?? cursor ?? null,
        })
      } catch (err) {
        queryLogger.warn('Activity conversations fetch failed', { error: err })
        throw err
      }
    },
    initialPageParam: cursor ?? null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled,
    retry: false,
  })
}

/**
 * `shouldFetch` lets a caller that hides the nav item skip the request; the
 * capability gate stays in force either way.
 */
export function useActivityQueueCount(shouldFetch = true) {
  const enabled = useActivityAccess() && shouldFetch
  return useQuery<{ count: number }>({
    queryKey: ['activity', 'queue-count'],
    queryFn: async () => {
      try {
        return await apiFetch<{ count: number }>('/api/app/activity/queue-count')
      } catch (err) {
        queryLogger.warn('Activity queue count fetch failed', { error: err })
        throw err
      }
    },
    enabled,
    retry: false,
  })
}

/**
 * The pair a knowledge admin records per assistant answer (SPEC Appendix A).
 * `cause` is always sent: `none` for correct/not_a_fault, never `none` for the
 * two failure verdicts.
 */
export type ConversationReviewVerdict = 'correct' | 'incomplete' | 'wrong' | 'not_a_fault'
export type ConversationReviewCause = 'knowledge_missing' | 'knowledge_wrong' | 'behaviour' | 'none'

export interface ConversationReview {
  verdict: ConversationReviewVerdict
  cause: ConversationReviewCause
  note: string | null
  kb_slug: string | null
  reviewer_name: string | null
  reviewed_at: string | null
}

export interface ConversationReviewInput {
  verdict: ConversationReviewVerdict
  cause: ConversationReviewCause
  note: string | null
  kb_slug: string | null
}

/** One message of the detail payload: the transcript shape plus the admin review. */
export interface ConversationDetailMessage extends ConversationMessage {
  review: ConversationReview | null
}

/** `GET /api/app/activity/conversations/{id}`. */
export interface ConversationDetail {
  id: number
  widget_id: string
  widget_name: string
  channel: 'webchat' | 'librechat'
  started_at: string
  language: string | null
  /** Only present when the backend exposes the visitor to this role. */
  visitor?: { name: string | null; email: string | null } | null
  quality: ConversationQuality | null
  messages: ConversationDetailMessage[]
}

/** Org knowledge bases offered by the review's `kb_slug` picker. */
export interface KnowledgeBaseOption {
  id: number
  name: string
  slug: string
  owner_type: string
}

export function useActivityConversation(conversationId: string | number) {
  const enabled = useActivityAccess()
  return useQuery<ConversationDetail, Error>({
    queryKey: ['activity', 'conversation', String(conversationId)],
    queryFn: async () => {
      try {
        return await apiFetch<ConversationDetail>(
          `/api/app/activity/conversations/${conversationId}`,
        )
      } catch (err) {
        queryLogger.warn('Activity conversation fetch failed', { error: err })
        throw err
      }
    },
    enabled,
    retry: false,
  })
}

export function useActivityKnowledgeBases() {
  const enabled = useActivityAccess()
  return useQuery<{ knowledge_bases: KnowledgeBaseOption[] }>({
    queryKey: ['activity', 'knowledge-bases'],
    queryFn: async () => {
      try {
        return await apiFetch<{ knowledge_bases: KnowledgeBaseOption[] }>(
          '/api/app/knowledge-bases',
        )
      } catch (err) {
        queryLogger.warn('Activity knowledge bases fetch failed', { error: err })
        throw err
      }
    },
    enabled,
    retry: false,
  })
}

/**
 * Writing a review changes what the detail shows, what the list shows as
 * reviewed, and how many conversations are still in the queue.
 */
function invalidateActivityViews(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ['activity', 'conversation'] })
  void queryClient.invalidateQueries({ queryKey: ['activity', 'conversations'] })
  void queryClient.invalidateQueries({ queryKey: ['activity', 'queue-count'] })
}

export function useUpsertReview(messageId: number) {
  const queryClient = useQueryClient()
  return useMutation<ConversationReview, Error, ConversationReviewInput>({
    mutationFn: (input) =>
      apiFetch<ConversationReview>(`/api/app/activity/messages/${messageId}/review`, {
        method: 'PUT',
        body: JSON.stringify(input),
      }),
    onSuccess: () => invalidateActivityViews(queryClient),
  })
}

export function useDeleteReview(messageId: number) {
  const queryClient = useQueryClient()
  return useMutation<null, Error>({
    mutationFn: () =>
      apiFetch<null>(`/api/app/activity/messages/${messageId}/review`, { method: 'DELETE' }),
    onSuccess: () => invalidateActivityViews(queryClient),
  })
}
