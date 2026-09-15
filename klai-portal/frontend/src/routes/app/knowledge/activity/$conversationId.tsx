// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3 + Appendix A: the conversation detail behind
// the knowledge activity list. One screen per conversation: the judge's verdict
// on the whole conversation, the transcript, and under every assistant answer
// its confidence signals plus the review form a knowledge admin fills in.
import { createFileRoute, Link } from '@tanstack/react-router'
import { AlertTriangle, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Tooltip } from '@/components/ui/tooltip'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { ConversationTranscript, QualityPanel } from '@/features/chat-activity'
import { useActivityConversation } from '@/features/chat-activity/api'
import { AnswerSignals } from '@/features/chat-activity/AnswerSignals'
import { ReviewForm } from '@/features/chat-activity/ReviewForm'
import * as m from '@/paraglide/messages'

/** The list's own filter keys; whatever comes along is carried back to it. */
const LIST_SEARCH_KEYS = [
  'days',
  'widget_id',
  'language',
  'judge_outcome',
  'failure_category',
  'review_status',
  'cause',
  'band',
  'rating',
  'queue',
  'sort',
  'cursor',
] as const

type ActivityDetailSearch = {
  // Null when the conversation was opened directly, so back goes to a clean list.
  list: Record<string, string | number | boolean> | null
}

const carriedSearch = (search: Record<string, unknown>): ActivityDetailSearch['list'] => {
  const carried: Record<string, string | number | boolean> = {}
  for (const key of LIST_SEARCH_KEYS) {
    const value = search[key]
    if (typeof value === 'string' && value !== '') {
      // `days` is numeric on the list; keep the number so the list revalidates it.
      carried[key] = key === 'days' && Number.isFinite(Number(value)) ? Number(value) : value
    }
  }
  return Object.keys(carried).length > 0 ? carried : null
}

export const Route = createFileRoute('/app/knowledge/activity/$conversationId')({
  validateSearch: (search: Record<string, unknown>): ActivityDetailSearch => ({
    list: carriedSearch(search),
  }),
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <ActivityDetailPage />
      </RoleGuard>
    </ProductGuard>
  ),
})

export function ActivityDetailPage() {
  const { conversationId } = Route.useParams()
  const { list: listSearch } = Route.useSearch()
  const { user } = useCurrentUser()
  // Same capability gate as the list (SPEC §4.3 access model).
  const hasActivityCapability = user?.hasCapability('kb.activity') === true
  const conversation = useActivityConversation(conversationId)

  if (!hasActivityCapability) {
    return (
      <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl/none font-semibold text-gray-900">{m.activity_page_title()}</h1>
        </div>
        <Tooltip label={m.capability_tooltip_knowledge_only()}>
          <p className="text-sm text-gray-600">{m.capability_tooltip_knowledge_only()}</p>
        </Tooltip>
      </div>
    )
  }

  const detail = conversation.data
  const firstQuestion = detail?.messages.find((message) => message.role === 'user')?.content
  const visitor = detail && 'visitor' in detail ? detail.visitor : null
  const backLabel = m.activity_detail_back()

  return (
    <PageContainer width="4xl" gap="6">
      <PageHeader
        title={firstQuestion ?? m.activity_page_title()}
        description={detail?.widget_name}
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link to="/app/knowledge/activity" search={listSearch ?? undefined}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              {backLabel}
            </Link>
          </Button>
        }
      />

      {conversation.isError ? (
        <QueryErrorState
          error={conversation.error}
          onRetry={() => void conversation.refetch()}
        />
      ) : conversation.isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : !detail ? (
        <ListEmptyState title={m.activity_detail_missing()} />
      ) : (
        <>
          {visitor && (
            <p className="text-sm text-gray-600">
              {m.activity_detail_visitor()}:{' '}
              <span className="text-gray-900">{visitor.name ?? visitor.email}</span>
              {visitor.name && visitor.email ? ` · ${visitor.email}` : ''}
            </p>
          )}

          {detail.quality && <QualityPanel quality={detail.quality} />}

          <div className="space-y-4">
            {detail.messages.map((message) => (
              <div key={message.id}>
                <ConversationTranscript messages={[message]} />
                {message.role === 'assistant' && (
                  <div className="mr-auto max-w-[85%]">
                    <AnswerSignals signals={message.answer_signals ?? null} />
                    <ReviewForm
                      messageId={message.id}
                      review={message.review ?? null}
                      quality={detail.quality}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </PageContainer>
  )
}
