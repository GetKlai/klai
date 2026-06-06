import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bug, CheckCheck, Download, Lightbulb, Loader2, MessageSquare, Send, Settings, SlidersHorizontal } from 'lucide-react'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useLocale } from '@/lib/locale'
import * as m from '@/paraglide/messages'
import { ApiError, apiFetch } from '@/lib/apiFetch'

type TabId = 'settings' | 'messages' | 'feedback' | 'advanced'

const VALID_TABS = new Set<TabId>(['settings', 'messages', 'feedback', 'advanced'])

type AccountSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/app/account')({
  validateSearch: (search: Record<string, unknown>): AccountSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: AccountPage,
})

interface MeAccount {
  preferred_language?: 'nl' | 'en'
  name?: string
  email?: string
}

interface AccountFeedbackUpdate {
  submission_id: number
  source: string
  raw_text: string
  submission_status: string
  created_at: string
  updated_at: string
  page_url?: string | null
  route_id?: string | null
  item_id?: number | null
  item_kind?: string | null
  item_title?: string | null
  item_summary?: string | null
  item_status?: string | null
  item_updated_at?: string | null
  notification_id?: number | null
  notification_body?: string | null
  notification_read_at?: string | null
  message_thread_id?: number | null
  latest_update_at: string
  unread: boolean
}

interface AccountFeedbackUpdatesResponse {
  items: AccountFeedbackUpdate[]
  unread_count: number
}

interface AccountPlatformMessageThread {
  id: number
  subject: string
  status: string
  origin_type: string
  feedback_submission_id?: number | null
  feedback_item_id?: number | null
  latest_message_body: string
  latest_message_sender_type: string
  latest_message_at: string
  last_read_at?: string | null
  unread: boolean
  created_at: string
}

interface AccountPlatformMessage {
  id: number
  sender_type: string
  sender_user_id?: string | null
  body: string
  created_at: string
}

interface AccountPlatformMessagesResponse {
  items: AccountPlatformMessageThread[]
  unread_count: number
}

interface AccountPlatformMessageThreadDetail {
  thread: AccountPlatformMessageThread
  messages: AccountPlatformMessage[]
}

function AccountPage() {
  const auth = useAuth()
  const { locale, switchLocale } = useLocale()
  const search = Route.useSearch()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [saved, setSaved] = useState(false)
  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>(locale)
  const [selectedThreadId, setSelectedThreadId] = useState<number | null>(null)
  const activeTab: TabId = search.tab ?? 'settings'

  // Fetch current user's preferred language from the portal DB
  const { data: meData } = useQuery({
    queryKey: ['me-language'],
    queryFn: async () => {
      try {
        return await apiFetch<MeAccount>(`/api/me`)
      } catch {
        return null
      }
    },
    enabled: auth.isAuthenticated,
  })

  const { data: feedbackUpdates, isLoading: feedbackLoading, error: feedbackError } = useQuery({
    queryKey: ['account-feedback-updates'],
    queryFn: () => apiFetch<AccountFeedbackUpdatesResponse>('/api/app/account/feedback-updates'),
    enabled: auth.isAuthenticated,
  })

  const { data: platformMessages, isLoading: messagesLoading, error: messagesError } = useQuery({
    queryKey: ['account-platform-messages'],
    queryFn: () => apiFetch<AccountPlatformMessagesResponse>('/api/app/account/messages'),
    enabled: auth.isAuthenticated,
  })

  const selectedThreadQuery = useQuery({
    queryKey: ['account-platform-message-thread', selectedThreadId],
    queryFn: () => apiFetch<AccountPlatformMessageThreadDetail>(`/api/app/account/messages/${selectedThreadId}`),
    enabled: auth.isAuthenticated && selectedThreadId !== null,
  })

  useEffect(() => {
    if (meData?.preferred_language) {
      setSelectedLang(meData.preferred_language)
    }
  }, [meData])

  const saveMutation = useMutation({
    mutationFn: async (preferred_language: 'nl' | 'en') => {
      await apiFetch(`/api/me/language`, {
        method: 'PATCH',
        body: JSON.stringify({ preferred_language }),
      })
      return preferred_language
    },
    onSuccess: (lang) => {
      switchLocale(lang)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
  })

  const sarMutation = useMutation({
    mutationFn: async () => {
      return apiFetch(`/api/me/sar-export`, { method: 'POST' })
    },
    onSuccess: (data: unknown) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const date = new Date().toISOString().split('T')[0]
      a.download = `sar-export-${date}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    },
  })

  const markFeedbackReadMutation = useMutation({
    mutationFn: async (notificationId: number) => {
      return apiFetch(`/api/app/account/feedback-updates/${notificationId}/read`, { method: 'POST' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-feedback-updates'] })
    },
  })

  const markAllFeedbackReadMutation = useMutation({
    mutationFn: async () => {
      return apiFetch('/api/app/account/feedback-updates/read-all', { method: 'POST' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-feedback-updates'] })
    },
  })

  const markMessageReadMutation = useMutation({
    mutationFn: async (threadId: number) => {
      return apiFetch(`/api/app/account/messages/${threadId}/read`, { method: 'POST' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
  })

  const markAllMessagesReadMutation = useMutation({
    mutationFn: async () => {
      return apiFetch('/api/app/account/messages/read-all', { method: 'POST' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
  })

  const replyMessageMutation = useMutation({
    mutationFn: async (vars: { threadId: number; body: string }) => {
      return apiFetch(`/api/app/account/messages/${vars.threadId}/reply`, {
        method: 'POST',
        body: JSON.stringify({ body: vars.body }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
  })

  // Name and email come from /api/me (sourced server-side from the Zitadel
  // userinfo claims). The BFF session only carries `sub`, so reading from
  // auth.user.profile here would always be empty.
  const name = meData?.name ?? ''
  const email = meData?.email ?? ''
  const hasProfileInfo = Boolean(name || email)
  const feedbackUnreadCount = feedbackUpdates?.unread_count ?? 0
  const messageUnreadCount = platformMessages?.unread_count ?? 0

  useEffect(() => {
    if (activeTab === 'messages' && selectedThreadId === null && platformMessages?.items.length) {
      setSelectedThreadId(platformMessages.items[0].id)
    }
  }, [activeTab, platformMessages?.items, selectedThreadId])

  const tabs: TabItem<TabId>[] = [
    { id: 'settings', label: m.account_tab_settings(), icon: Settings },
    {
      id: 'messages',
      label: m.account_tab_messages(),
      icon: MessageSquare,
      notificationCount: messageUnreadCount,
      notificationLabel: m.account_messages_unread(),
    },
    {
      id: 'feedback',
      label: m.account_tab_feedback(),
      icon: MessageSquare,
      notificationCount: feedbackUnreadCount,
      notificationLabel: m.account_feedback_unread(),
    },
    { id: 'advanced', label: m.account_tab_advanced(), icon: SlidersHorizontal },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/app/account',
      search: { tab },
    })
  }

  const wideLayout = activeTab === 'messages' || activeTab === 'feedback'

  return (
    <div className={`mx-auto ${wideLayout ? 'max-w-5xl' : 'max-w-2xl'} px-6 pt-4 pb-10 space-y-8`}>
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.account_heading()}
        </h1>
        <p className="text-sm text-gray-400">
          {m.account_subtitle()}
        </p>
      </div>

      <Tabs tabs={tabs} value={activeTab} onValueChange={setTab} />

      {activeTab === 'settings' && (
        <div className="space-y-6" data-help-id="account-2fa">
          {hasProfileInfo && (
            <div className="border-b border-gray-200 pb-6">
              <dl className="space-y-3">
                {name && (
                  <div className="flex flex-col gap-1 sm:flex-row sm:gap-4">
                    <dt className="w-32 shrink-0 text-sm text-gray-400">Naam</dt>
                    <dd className="text-sm font-medium text-gray-900">{name}</dd>
                  </div>
                )}
                {email && (
                  <div className="flex flex-col gap-1 sm:flex-row sm:gap-4">
                    <dt className="w-32 shrink-0 text-sm text-gray-400">E-mail</dt>
                    <dd className="text-sm font-medium text-gray-900">{email}</dd>
                  </div>
                )}
              </dl>
            </div>
          )}

          <div>
            <h2 className="text-sm font-medium text-gray-900 mb-2">
              {m.account_language_title()}
            </h2>
            <p className="text-sm text-gray-400 mb-6">
              {m.account_language_description()}
            </p>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1 space-y-1.5">
                <Label htmlFor="account-language">
                  {m.account_language_label()}
                </Label>
                <Select
                  id="account-language"
                  value={selectedLang}
                  onChange={(e) => setSelectedLang(e.target.value as 'nl' | 'en')}
                  containerClassName="max-w-xs"
                >
                  <option value="nl">{m.account_language_nl()}</option>
                  <option value="en">{m.account_language_en()}</option>
                </Select>
              </div>
              <Button
                className="w-fit"
                onClick={() => saveMutation.mutate(selectedLang)}
                disabled={saveMutation.isPending || saved}
              >
                {saved
                  ? m.account_saved()
                  : saveMutation.isPending
                    ? m.account_saving()
                    : m.account_save()}
              </Button>
            </div>
            {saveMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.account_error_save()}</p>
            )}
          </div>
        </div>
      )}

      {activeTab === 'feedback' && (
        <FeedbackUpdatesPanel
          items={feedbackUpdates?.items ?? []}
          isLoading={feedbackLoading}
          error={feedbackError}
          locale={locale}
          unreadCount={feedbackUnreadCount}
          onMarkRead={(notificationId) => markFeedbackReadMutation.mutate(notificationId)}
          onMarkAllRead={() => markAllFeedbackReadMutation.mutate()}
          isMarkingAllRead={markAllFeedbackReadMutation.isPending}
        />
      )}

      {activeTab === 'messages' && (
        <AccountMessagesPanel
          items={platformMessages?.items ?? []}
          detail={selectedThreadQuery.data ?? null}
          selectedThreadId={selectedThreadId}
          isLoading={messagesLoading}
          detailLoading={selectedThreadQuery.isLoading}
          error={messagesError}
          locale={locale}
          unreadCount={messageUnreadCount}
          onMarkAllRead={() => markAllMessagesReadMutation.mutate()}
          onSelect={(thread) => {
            setSelectedThreadId(thread.id)
            if (thread.unread) markMessageReadMutation.mutate(thread.id)
          }}
          onReply={(body) => {
            if (selectedThreadId !== null) {
              replyMessageMutation.mutate({ threadId: selectedThreadId, body })
            }
          }}
          isReplying={replyMessageMutation.isPending}
          isMarkingAllRead={markAllMessagesReadMutation.isPending}
        />
      )}

      {activeTab === 'advanced' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-sm font-medium text-gray-900 mb-2">{m.account_sar_title()}</h2>
            <p className="text-sm text-gray-400 mb-4">
              {m.account_sar_description()}
            </p>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => sarMutation.mutate()}
              disabled={sarMutation.isPending}
            >
              <Download className="h-4 w-4 mr-2" />
              {sarMutation.isPending ? m.account_sar_downloading() : m.account_sar_button()}
            </Button>
            {sarMutation.error && (
              <p className="mt-3 text-sm text-[var(--color-destructive)]">
                {sarMutation.error instanceof ApiError && sarMutation.error.status === 429
                  ? m.account_sar_rate_limited()
                  : m.account_sar_error()}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function AccountMessagesPanel({
  items,
  detail,
  selectedThreadId,
  isLoading,
  detailLoading,
  error,
  locale,
  unreadCount,
  onMarkAllRead,
  onSelect,
  onReply,
  isReplying,
  isMarkingAllRead,
}: {
  items: AccountPlatformMessageThread[]
  detail: AccountPlatformMessageThreadDetail | null
  selectedThreadId: number | null
  isLoading: boolean
  detailLoading: boolean
  error: unknown
  locale: 'nl' | 'en'
  unreadCount: number
  onMarkAllRead: () => void
  onSelect: (thread: AccountPlatformMessageThread) => void
  onReply: (body: string) => void
  isReplying: boolean
  isMarkingAllRead: boolean
}) {
  const [replyBody, setReplyBody] = useState('')
  const hasError = error != null

  function submitReply() {
    const trimmed = replyBody.trim()
    if (!trimmed) return
    onReply(trimmed)
    setReplyBody('')
  }

  return (
    <div className="space-y-6">
      <PanelHeader
        title={m.account_messages_title()}
        description={m.account_messages_description()}
        unreadCount={unreadCount}
        markAllLabel={m.account_messages_mark_all_read()}
        onMarkAllRead={onMarkAllRead}
        isMarkingAllRead={isMarkingAllRead}
      />

      {isLoading && <SplitLoadingState detailPreview />}

      {!isLoading && hasError && (
        <p className="text-sm text-[var(--color-destructive)]">{m.account_messages_error()}</p>
      )}

      {!isLoading && !hasError && items.length === 0 && (
        <EmptyState
          title={m.account_messages_empty_title()}
          description={m.account_messages_empty_description()}
        />
      )}

      {!isLoading && !hasError && items.length > 0 && (
        <div className="grid min-h-[560px] border-y border-gray-200 lg:grid-cols-[minmax(240px,320px)_1fr]">
          <div className="divide-y divide-gray-100 lg:border-r lg:border-gray-200">
            {items.map((thread) => {
              const selected = thread.id === selectedThreadId
              return (
                <button
                  key={thread.id}
                  type="button"
                  onClick={() => onSelect(thread)}
                  className={`flex w-full gap-3 px-3 py-4 text-left transition-colors hover:bg-gray-50 ${selected ? 'bg-gray-50' : ''}`}
                >
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500">
                    <MessageSquare className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      {thread.unread && <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-success)]" />}
                      <h3 className="truncate text-sm font-medium text-gray-900">{thread.subject}</h3>
                    </div>
                    <p className="mt-1 line-clamp-2 text-sm text-gray-500">{thread.latest_message_body}</p>
                    <p className="mt-2 text-xs text-gray-400">
                      {formatFeedbackDate(thread.latest_message_at, locale)}
                    </p>
                  </div>
                </button>
              )
            })}
          </div>

          <div className="min-w-0 p-4">
            {selectedThreadId === null ? (
              <ThreadSelectEmpty label={m.account_messages_select_thread()} />
            ) : (
              <ConversationThread
                detail={detail}
                detailLoading={detailLoading}
                locale={locale}
                replyBody={replyBody}
                onReplyBodyChange={setReplyBody}
                onSubmitReply={submitReply}
                isReplying={isReplying}
                textareaId="account-message-reply"
                replyLabel={m.account_messages_reply()}
                sendLabel={m.account_messages_send()}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function ConversationThread({
  detail,
  detailLoading,
  locale,
  replyBody,
  onReplyBodyChange,
  onSubmitReply,
  isReplying,
  textareaId,
  replyLabel,
  sendLabel,
}: {
  detail: AccountPlatformMessageThreadDetail | null
  detailLoading: boolean
  locale: 'nl' | 'en'
  replyBody: string
  onReplyBodyChange: (value: string) => void
  onSubmitReply: () => void
  isReplying: boolean
  textareaId: string
  replyLabel: string
  sendLabel: string
}) {
  if (detailLoading || !detail) {
    return (
      <div className="flex min-h-[320px] items-center justify-center gap-2 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        {m.admin_shared_loading()}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-[520px] flex-col">
      <div className="border-b border-gray-200 pb-4">
        <h3 className="text-base font-display-bold text-gray-900">{detail.thread.subject}</h3>
        <p className="mt-1 text-xs text-gray-400">
          {m.account_messages_started()} {formatFeedbackDate(detail.thread.created_at, locale)}
        </p>
      </div>

      <div className="min-h-[260px] flex-1 overflow-y-auto py-4">
        <div className="space-y-3">
          {detail.messages.map((message) => {
            const isUser = message.sender_type === 'user'
            return (
              <article key={message.id} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                <div className="max-w-[78%]">
                  <p className={`mb-1 text-xs ${isUser ? 'text-right text-gray-400' : 'text-gray-400'}`}>
                    {isUser ? m.account_messages_you() : m.account_messages_platform_admin()} ·{' '}
                    {formatFeedbackDate(message.created_at, locale)}
                  </p>
                  <div className={`rounded-lg px-3 py-2 text-sm leading-6 ${isUser ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-900'}`}>
                    <p className="whitespace-pre-wrap">{message.body}</p>
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      </div>

      <ReplyComposer
        textareaId={textareaId}
        label={replyLabel}
        buttonLabel={sendLabel}
        value={replyBody}
        onChange={onReplyBodyChange}
        onSubmit={onSubmitReply}
        isSubmitting={isReplying}
      />
    </div>
  )
}

function ReplyComposer({
  textareaId,
  label,
  buttonLabel,
  value,
  onChange,
  onSubmit,
  isSubmitting,
}: {
  textareaId: string
  label: string
  buttonLabel: string
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  isSubmitting: boolean
}) {
  return (
    <div className="space-y-2 border-t border-gray-200 pt-4">
      <Label htmlFor={textareaId}>{label}</Label>
      <Textarea
        id={textareaId}
        rows={4}
        value={value}
        maxLength={4000}
        onChange={(event) => onChange(event.target.value)}
      />
      <Button
        type="button"
        disabled={value.trim().length === 0 || isSubmitting}
        onClick={onSubmit}
      >
        {isSubmitting ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Send className="h-4 w-4" />
        )}
        {buttonLabel}
      </Button>
    </div>
  )
}

function FeedbackUpdatesPanel({
  items,
  isLoading,
  error,
  locale,
  unreadCount,
  onMarkRead,
  onMarkAllRead,
  isMarkingAllRead,
}: {
  items: AccountFeedbackUpdate[]
  isLoading: boolean
  error: unknown
  locale: 'nl' | 'en'
  unreadCount: number
  onMarkRead: (notificationId: number) => void
  onMarkAllRead: () => void
  isMarkingAllRead: boolean
}) {
  const queryClient = useQueryClient()
  const [selectedSubmissionId, setSelectedSubmissionId] = useState<number | null>(null)
  const [replyBody, setReplyBody] = useState('')
  const [createdThreadIds, setCreatedThreadIds] = useState<Record<number, number>>({})
  const hasError = error != null
  const selectedItem = items.find((item) => item.submission_id === selectedSubmissionId) ?? null
  const selectedThreadId = selectedItem
    ? createdThreadIds[selectedItem.submission_id] ?? selectedItem.message_thread_id ?? null
    : null

  useEffect(() => {
    if (items.length === 0) {
      setSelectedSubmissionId(null)
      return
    }
    if (selectedSubmissionId === null || !items.some((item) => item.submission_id === selectedSubmissionId)) {
      setSelectedSubmissionId(items[0].submission_id)
    }
  }, [items, selectedSubmissionId])

  const selectedFeedbackThreadQuery = useQuery({
    queryKey: ['account-platform-message-thread', selectedThreadId],
    queryFn: () => apiFetch<AccountPlatformMessageThreadDetail>(`/api/app/account/messages/${selectedThreadId}`),
    enabled: selectedThreadId !== null,
  })

  const feedbackReplyMutation = useMutation({
    mutationFn: async (vars: { submissionId: number; body: string }) => {
      return apiFetch<AccountPlatformMessageThreadDetail>(`/api/app/account/feedback-updates/${vars.submissionId}/reply`, {
        method: 'POST',
        body: JSON.stringify({ body: vars.body }),
      })
    },
    onSuccess: (detail, vars) => {
      setCreatedThreadIds((previous) => ({
        ...previous,
        [vars.submissionId]: detail.thread.id,
      }))
      setReplyBody('')
      void queryClient.invalidateQueries({ queryKey: ['account-feedback-updates'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
  })

  function selectItem(item: AccountFeedbackUpdate) {
    setSelectedSubmissionId(item.submission_id)
    if (item.unread && item.notification_id !== null && item.notification_id !== undefined) {
      onMarkRead(item.notification_id)
    }
  }

  function submitFeedbackReply() {
    const trimmed = replyBody.trim()
    if (!trimmed || selectedItem === null) return
    feedbackReplyMutation.mutate({ submissionId: selectedItem.submission_id, body: trimmed })
  }

  return (
    <div className="space-y-6">
      <PanelHeader
        title={m.account_feedback_title()}
        description={m.account_feedback_description()}
        unreadCount={unreadCount}
        markAllLabel={m.account_feedback_mark_all_read()}
        onMarkAllRead={onMarkAllRead}
        isMarkingAllRead={isMarkingAllRead}
      />

      {isLoading && <SplitLoadingState />}

      {!isLoading && hasError && (
        <p className="text-sm text-[var(--color-destructive)]">{m.account_feedback_error()}</p>
      )}

      {!isLoading && !hasError && items.length === 0 && (
        <EmptyState
          title={m.account_feedback_empty_title()}
          description={m.account_feedback_empty_description()}
        />
      )}

      {!isLoading && !hasError && items.length > 0 && (
        <div className="grid min-h-[620px] border-y border-gray-200 lg:grid-cols-[minmax(260px,360px)_1fr]">
          <div className="divide-y divide-gray-100 lg:border-r lg:border-gray-200">
            {items.map((item) => (
              <FeedbackUpdateRow
                key={item.submission_id}
                item={item}
                locale={locale}
                selected={item.submission_id === selectedSubmissionId}
                onSelect={() => selectItem(item)}
              />
            ))}
          </div>
          <div className="min-w-0 p-4">
            {selectedItem === null ? (
              <ThreadSelectEmpty label={m.account_feedback_select_report()} />
            ) : (
              <FeedbackConversationPanel
                item={selectedItem}
                threadDetail={selectedFeedbackThreadQuery.data ?? null}
                threadLoading={selectedThreadId !== null && selectedFeedbackThreadQuery.isLoading}
                hasThread={selectedThreadId !== null}
                locale={locale}
                replyBody={replyBody}
                onReplyBodyChange={setReplyBody}
                onSubmitReply={submitFeedbackReply}
                isReplying={feedbackReplyMutation.isPending}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function FeedbackConversationPanel({
  item,
  threadDetail,
  threadLoading,
  hasThread,
  locale,
  replyBody,
  onReplyBodyChange,
  onSubmitReply,
  isReplying,
}: {
  item: AccountFeedbackUpdate
  threadDetail: AccountPlatformMessageThreadDetail | null
  threadLoading: boolean
  hasThread: boolean
  locale: 'nl' | 'en'
  replyBody: string
  onReplyBodyChange: (value: string) => void
  onSubmitReply: () => void
  isReplying: boolean
}) {
  const title = item.item_title || truncateText(item.raw_text, 88)
  const status = feedbackStatusLabel(item)

  return (
    <div className="flex h-full min-h-[580px] flex-col">
      <div className="border-b border-gray-200 pb-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h3 className="text-base font-display-bold text-gray-900">{title}</h3>
            <p className="mt-1 text-xs text-gray-400">
              {m.account_feedback_reported()} {formatFeedbackDate(item.created_at, locale)}
            </p>
          </div>
          <Badge variant={status.variant} className="w-fit shrink-0">
            {status.label}
          </Badge>
        </div>
      </div>

      <div className="border-b border-gray-200 py-4">
        <p className="text-xs font-medium text-gray-400">{m.account_feedback_original_report()}</p>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-900">{item.raw_text}</p>
        {item.notification_body && (
          <p className="mt-3 border-l-2 border-gray-200 pl-3 text-sm leading-6 text-gray-600">
            {item.notification_body}
          </p>
        )}
      </div>

      {hasThread ? (
        <div className="min-h-0 flex-1 pt-4">
          <ConversationThread
            detail={threadDetail}
            detailLoading={threadLoading}
            locale={locale}
            replyBody={replyBody}
            onReplyBodyChange={onReplyBodyChange}
            onSubmitReply={onSubmitReply}
            isReplying={isReplying}
            textareaId={`account-feedback-reply-${item.submission_id}`}
            replyLabel={m.account_feedback_reply()}
            sendLabel={m.account_feedback_send()}
          />
        </div>
      ) : (
        <div className="flex flex-1 flex-col justify-between pt-4">
          <p className="text-sm text-gray-400">{m.account_feedback_no_thread()}</p>
          <ReplyComposer
            textareaId={`account-feedback-reply-${item.submission_id}`}
            label={m.account_feedback_reply()}
            buttonLabel={m.account_feedback_send()}
            value={replyBody}
            onChange={onReplyBodyChange}
            onSubmit={onSubmitReply}
            isSubmitting={isReplying}
          />
        </div>
      )}
    </div>
  )
}

function FeedbackUpdateRow({
  item,
  locale,
  selected,
  onSelect,
}: {
  item: AccountFeedbackUpdate
  locale: 'nl' | 'en'
  selected: boolean
  onSelect: () => void
}) {
  const title = item.item_title || truncateText(item.raw_text, 88)
  const status = feedbackStatusLabel(item)
  const updatedAt = formatFeedbackDate(item.latest_update_at, locale)
  const createdAt = formatFeedbackDate(item.created_at, locale)

  return (
    <button
      type="button"
      className={`flex w-full gap-3 px-3 py-4 text-left transition-colors hover:bg-gray-50 ${selected ? 'bg-gray-50' : ''}`}
      onClick={onSelect}
    >
      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500">
        {item.source === 'assistant_problem' ? <Bug className="h-4 w-4" /> : <Lightbulb className="h-4 w-4" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {item.unread && <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-success)]" />}
              <h3 className="text-sm font-medium text-gray-900">{title}</h3>
            </div>
            {item.notification_body && (
              <p className="mt-1 text-sm text-gray-900">{item.notification_body}</p>
            )}
            <p className="mt-1 line-clamp-2 text-sm text-gray-500">{item.raw_text}</p>
          </div>
          <Badge variant={status.variant} className="shrink-0">
            {status.label}
          </Badge>
        </div>
        <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
          <div className="flex gap-1">
            <dt>{m.account_feedback_reported()}</dt>
            <dd>{createdAt}</dd>
          </div>
          <div className="flex gap-1">
            <dt>{m.account_feedback_updated()}</dt>
            <dd>{updatedAt}</dd>
          </div>
        </dl>
      </div>
    </button>
  )
}

function PanelHeader({
  title,
  description,
  unreadCount,
  markAllLabel,
  onMarkAllRead,
  isMarkingAllRead,
}: {
  title: string
  description: string
  unreadCount: number
  markAllLabel: string
  onMarkAllRead: () => void
  isMarkingAllRead: boolean
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h2 className="text-sm font-medium text-gray-900 mb-2">{title}</h2>
        <p className="text-sm text-gray-400">{description}</p>
      </div>
      {unreadCount > 0 && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="w-fit shrink-0"
          onClick={onMarkAllRead}
          disabled={isMarkingAllRead}
        >
          {isMarkingAllRead ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />}
          {markAllLabel}
        </Button>
      )}
    </div>
  )
}

function SplitLoadingState({ detailPreview = false }: { detailPreview?: boolean }) {
  return (
    <div className="grid border-y border-gray-200 lg:grid-cols-[minmax(260px,360px)_1fr]">
      <div className="divide-y divide-gray-100 lg:border-r lg:border-gray-200">
        {[0, 1, 2].map((index) => (
          <div key={index} className="flex gap-3 px-3 py-4">
            <div className="mt-0.5 h-8 w-8 shrink-0 rounded-full bg-gray-100" />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="h-4 w-2/3 rounded bg-gray-100" />
              <div className="h-3 w-full rounded bg-gray-100" />
              <div className="h-3 w-1/3 rounded bg-gray-100" />
            </div>
          </div>
        ))}
      </div>
      {detailPreview && (
        <div className="hidden p-4 lg:block">
          <div className="h-4 w-1/2 rounded bg-gray-100" />
          <div className="mt-6 space-y-3">
            <div className="h-16 w-2/3 rounded-lg bg-gray-100" />
            <div className="ml-auto h-16 w-2/3 rounded-lg bg-gray-100" />
          </div>
        </div>
      )}
    </div>
  )
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="border-y border-gray-200 py-8">
      <p className="text-sm font-medium text-gray-900">{title}</p>
      <p className="mt-1 text-sm text-gray-400">{description}</p>
    </div>
  )
}

function ThreadSelectEmpty({ label }: { label: string }) {
  return (
    <div className="flex h-full min-h-[320px] items-center justify-center text-sm text-gray-400">
      {label}
    </div>
  )
}

function feedbackStatusLabel(item: AccountFeedbackUpdate): {
  label: string
  variant: BadgeProps['variant']
} {
  const status = item.item_status ?? item.submission_status

  if (status === 'resolved') {
    return { label: m.account_feedback_status_fixed(), variant: 'success' }
  }
  if (status === 'open') {
    return { label: m.account_feedback_status_review(), variant: 'warning' }
  }
  if (status === 'dismissed') {
    return { label: m.account_feedback_status_closed(), variant: 'secondary' }
  }
  if (status === 'support') {
    return { label: m.account_feedback_status_support(), variant: 'info' }
  }
  return { label: m.account_feedback_status_received(), variant: 'secondary' }
}

function formatFeedbackDate(value: string, locale: 'nl' | 'en') {
  return new Intl.DateTimeFormat(locale === 'nl' ? 'nl-NL' : 'en-US', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(value))
}

function truncateText(value: string, maxLength: number) {
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength - 3).trim()}...`
}
