// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3 + Appendix A: the conversation detail behind
// the knowledge activity list. One screen per conversation: the transcript on
// the left, and on the right the judge's verdict on the whole conversation
// plus the review form for one assistant answer at a time (the last one by
// default, switchable per bubble).
import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
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
import { fetchMe } from '@/lib/api-me'
import { appNavActivityIsVisible } from '@/routes/app/-app-tools'
import { ConversationTranscript, QualityPanel } from '@/features/chat-activity'
import { useActivityConversation } from '@/features/chat-activity/api'
import { AnswerSignals } from '@/features/chat-activity/AnswerSignals'
import { ReviewForm } from '@/features/chat-activity/ReviewForm'
import * as m from '@/paraglide/messages'
import { parseActivitySearch } from './-search'

type ActivityDetailSearch = {
  // Undefined when the conversation was opened directly, so back goes to a
  // clean list. One flat string, not the list's filter object: main.tsx's
  // router-wide stringifySearch/parseSearch only supports flat values, and a
  // nested `{ list: {...} }` search serialises as "[object Object]".
  back?: string
}

export const Route = createFileRoute('/app/knowledge/activity/$conversationId')({
  validateSearch: (search: Record<string, unknown>): ActivityDetailSearch => ({
    back: typeof search.back === 'string' ? search.back : undefined,
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
  const { back } = Route.useSearch()
  const { user } = useCurrentUser()
  // Same capability gate as the list (SPEC §4.3 access model).
  const hasActivityCapability = user?.hasCapability('kb.activity') === true
  // Same tenant-unlock gate as the list and the sidebar (appNavActivityIsVisible,
  // -app-tools.ts): capability alone still lets useActivityConversation 403.
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasActivityCapability,
  })
  const isUnlocked = appNavActivityIsVisible({
    hasCapability: (cap) => user?.hasCapability(cap) === true,
    unlockedFeatures: meQuery.data?.platform_unlocked_features ?? [],
  })
  const conversation = useActivityConversation(conversationId)
  // Which assistant answer the review panel targets; null means "follow the
  // default" (the last assistant turn) rather than pinning turn 1 while the
  // conversation is still loading.
  const [selectedMessageId, setSelectedMessageId] = useState<number | null>(null)

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

  if (meQuery.isLoading) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }

  if (meQuery.isError) {
    // A failed /api/me read is not a missing unlock: say so and offer a retry.
    return (
      <PageContainer width="6xl" gap="6">
        <QueryErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />
      </PageContainer>
    )
  }

  if (!isUnlocked) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListEmptyState icon={AlertTriangle} title={m.activity_unlock_required()} />
      </PageContainer>
    )
  }

  const detail = conversation.data
  const firstQuestion = detail?.messages.find((message) => message.role === 'user')?.content
  const visitor = detail && 'visitor' in detail ? detail.visitor : null
  const backLabel = m.activity_detail_back()
  const assistantMessages = detail?.messages.filter((message) => message.role === 'assistant') ?? []
  const activeMessage =
    assistantMessages.find((message) => message.id === selectedMessageId) ?? assistantMessages.at(-1) ?? null

  return (
    <PageContainer width="6xl" gap="6">
      <PageHeader
        title={firstQuestion ?? m.activity_page_title()}
        description={detail?.widget_name}
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link to="/app/knowledge/activity" search={parseActivitySearch(Object.fromEntries(new URLSearchParams(back ?? '')))}>
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
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start">
          <div className="space-y-4 min-w-0">
            {visitor && (
              <p className="text-sm text-gray-600">
                {m.activity_detail_visitor()}:{' '}
                <span className="text-gray-900">{visitor.name ?? visitor.email}</span>
                {visitor.name && visitor.email ? ` · ${visitor.email}` : ''}
              </p>
            )}
            {detail.messages.map((message) => (
              <div key={message.id}>
                <ConversationTranscript messages={[message]} />
                {message.role === 'assistant' && (
                  <div className="mr-auto mt-1 max-w-[85%]">
                    <button
                      type="button"
                      aria-pressed={message.id === activeMessage?.id}
                      onClick={() => setSelectedMessageId(message.id)}
                      className={
                        message.id === activeMessage?.id
                          ? 'rounded-md border border-gray-900 bg-[var(--color-secondary)] px-2 py-1 text-xs font-medium text-gray-900'
                          : 'klai-hover rounded-md px-2 py-1 text-xs text-gray-600'
                      }
                    >
                      {message.id === activeMessage?.id
                        ? m.activity_detail_reviewing_this_answer()
                        : m.activity_detail_review_this_answer()}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="space-y-4 lg:sticky lg:top-6">
            {detail.quality && <QualityPanel quality={detail.quality} />}
            {activeMessage && (
              <>
                <AnswerSignals signals={activeMessage.answer_signals ?? null} />
                <ReviewForm
                  // A fresh form per answer: a draft for one answer must never
                  // be saved against another when the reviewer switches.
                  key={activeMessage.id}
                  messageId={activeMessage.id}
                  review={activeMessage.review ?? null}
                  quality={detail.quality}
                />
              </>
            )}
          </div>
        </div>
      )}
    </PageContainer>
  )
}
