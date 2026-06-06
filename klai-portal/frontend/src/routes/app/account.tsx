import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { type ReactNode, useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bug, CheckCheck, Download, Inbox, Lightbulb, Loader2, MessageSquare, Settings, SlidersHorizontal } from 'lucide-react'
import { toast } from 'sonner'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { ConversationPanel, type ConversationEntry } from '@/components/ui/conversation'
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

  const [saved, setSaved] = useState(false)
  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>(locale)
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

  // Name and email come from /api/me (sourced server-side from the Zitadel
  // userinfo claims). The BFF session only carries `sub`, so reading from
  // auth.user.profile here would always be empty.
  const name = meData?.name ?? ''
  const email = meData?.email ?? ''
  const hasProfileInfo = Boolean(name || email)
  const feedbackUnreadCount = feedbackUpdates?.unread_count ?? 0
  const messageUnreadCount = platformMessages?.unread_count ?? 0

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
      icon: Inbox,
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

  return (
    <div className="mx-auto max-w-2xl px-6 pt-4 pb-10 space-y-8">
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
        />
      )}

      {activeTab === 'messages' && (
        <AccountMessagesPanel
          items={platformMessages?.items ?? []}
          isLoading={messagesLoading}
          error={messagesError}
          locale={locale}
          unreadCount={messageUnreadCount}
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

// --- Berichten (direct platform-admin messages) ----------------------------

function AccountMessagesPanel({
  items,
  isLoading,
  error,
  locale,
  unreadCount,
}: {
  items: AccountPlatformMessageThread[]
  isLoading: boolean
  error: unknown
  locale: 'nl' | 'en'
  unreadCount: number
}) {
  const queryClient = useQueryClient()
  const [selectedThreadId, setSelectedThreadId] = useState<number | null>(null)
  const [replyBody, setReplyBody] = useState('')
  const hasError = error != null

  const detailQuery = useQuery({
    queryKey: ['account-platform-message-thread', selectedThreadId],
    queryFn: () => apiFetch<AccountPlatformMessageThreadDetail>(`/api/app/account/messages/${selectedThreadId}`),
    enabled: selectedThreadId !== null,
  })

  const markReadMutation = useMutation({
    mutationFn: (threadId: number) => apiFetch(`/api/app/account/messages/${threadId}/read`, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
  })

  const markAllReadMutation = useMutation({
    mutationFn: () => apiFetch('/api/app/account/messages/read-all', { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
  })

  const replyMutation = useMutation({
    mutationFn: (vars: { threadId: number; body: string }) =>
      apiFetch(`/api/app/account/messages/${vars.threadId}/reply`, {
        method: 'POST',
        body: JSON.stringify({ body: vars.body }),
      }),
    onSuccess: () => {
      setReplyBody('')
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
    onError: () => toast.error(m.account_messages_error()),
  })

  function openThread(thread: AccountPlatformMessageThread) {
    setSelectedThreadId(thread.id)
    setReplyBody('')
    if (thread.unread) markReadMutation.mutate(thread.id)
  }

  function back() {
    setSelectedThreadId(null)
    setReplyBody('')
  }

  function submitReply() {
    const trimmed = replyBody.trim()
    if (!trimmed || selectedThreadId === null) return
    replyMutation.mutate({ threadId: selectedThreadId, body: trimmed })
  }

  if (selectedThreadId !== null) {
    const detail = detailQuery.data ?? null
    const entries: ConversationEntry[] = (detail?.messages ?? []).map((message) => ({
      id: message.id,
      side: message.sender_type === 'user' ? 'me' : 'them',
      author: message.sender_type === 'user' ? m.account_messages_you() : m.account_messages_platform_admin(),
      body: message.body,
      at: message.created_at,
    }))

    return (
      <ConversationDetail
        title={detail?.thread.subject ?? ''}
        subtitle={
          detail
            ? `${m.account_messages_started()} ${formatFeedbackDate(detail.thread.created_at, locale)}`
            : undefined
        }
        entries={entries}
        loading={detailQuery.isLoading}
        locale={locale}
        replyBody={replyBody}
        onReplyBodyChange={setReplyBody}
        onSubmitReply={submitReply}
        isReplying={replyMutation.isPending}
        replyPlaceholder={m.account_messages_reply_placeholder()}
        sendLabel={m.account_messages_send()}
        onBack={back}
      />
    )
  }

  return (
    <div className="space-y-6">
      <PanelHeader
        title={m.account_messages_title()}
        description={m.account_messages_description()}
        unreadCount={unreadCount}
        markAllLabel={m.account_messages_mark_all_read()}
        onMarkAllRead={() => markAllReadMutation.mutate()}
        isMarkingAllRead={markAllReadMutation.isPending}
      />

      {isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : hasError ? (
        <p className="text-sm text-[var(--color-destructive)]">{m.account_messages_error()}</p>
      ) : items.length === 0 ? (
        <ListEmptyState
          title={m.account_messages_empty_title()}
          description={m.account_messages_empty_description()}
        />
      ) : (
        <div className="border-y border-gray-200 divide-y divide-gray-100">
          {items.map((thread) => (
            <FeedRow
              key={thread.id}
              icon={<MessageSquare className="h-4 w-4" />}
              unread={thread.unread}
              title={thread.subject}
              snippet={thread.latest_message_body}
              date={formatFeedbackDate(thread.latest_message_at, locale)}
              onOpen={() => openThread(thread)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// --- Mijn meldingen (feedback) + conversation ------------------------------

function FeedbackUpdatesPanel({
  items,
  isLoading,
  error,
  locale,
  unreadCount,
}: {
  items: AccountFeedbackUpdate[]
  isLoading: boolean
  error: unknown
  locale: 'nl' | 'en'
  unreadCount: number
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

  const threadQuery = useQuery({
    queryKey: ['account-platform-message-thread', selectedThreadId],
    queryFn: () => apiFetch<AccountPlatformMessageThreadDetail>(`/api/app/account/messages/${selectedThreadId}`),
    enabled: selectedThreadId !== null,
  })

  const markReadMutation = useMutation({
    mutationFn: (notificationId: number) =>
      apiFetch(`/api/app/account/feedback-updates/${notificationId}/read`, { method: 'POST' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['account-feedback-updates'] }),
  })

  const markAllReadMutation = useMutation({
    mutationFn: () => apiFetch('/api/app/account/feedback-updates/read-all', { method: 'POST' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['account-feedback-updates'] }),
  })

  const replyMutation = useMutation({
    mutationFn: (vars: { submissionId: number; body: string }) =>
      apiFetch<AccountPlatformMessageThreadDetail>(`/api/app/account/feedback-updates/${vars.submissionId}/reply`, {
        method: 'POST',
        body: JSON.stringify({ body: vars.body }),
      }),
    onSuccess: (detail, vars) => {
      setCreatedThreadIds((previous) => ({ ...previous, [vars.submissionId]: detail.thread.id }))
      setReplyBody('')
      void queryClient.invalidateQueries({ queryKey: ['account-feedback-updates'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-messages'] })
      void queryClient.invalidateQueries({ queryKey: ['account-platform-message-thread'] })
    },
    onError: () => toast.error(m.account_feedback_error()),
  })

  function openItem(item: AccountFeedbackUpdate) {
    setSelectedSubmissionId(item.submission_id)
    setReplyBody('')
    if (item.unread && item.notification_id != null) markReadMutation.mutate(item.notification_id)
  }

  function back() {
    setSelectedSubmissionId(null)
    setReplyBody('')
  }

  function submitReply() {
    const trimmed = replyBody.trim()
    if (!trimmed || selectedItem === null) return
    replyMutation.mutate({ submissionId: selectedItem.submission_id, body: trimmed })
  }

  if (selectedItem !== null) {
    const status = feedbackStatusLabel(selectedItem)
    const title = selectedItem.item_title || truncateText(selectedItem.raw_text, 88)
    const entries = buildFeedbackEntries(selectedItem, threadQuery.data ?? null)

    return (
      <ConversationDetail
        title={title}
        subtitle={`${m.account_feedback_reported()} ${formatFeedbackDate(selectedItem.created_at, locale)}`}
        badge={<Badge variant={status.variant}>{status.label}</Badge>}
        entries={entries}
        loading={selectedThreadId !== null && threadQuery.isLoading}
        locale={locale}
        replyBody={replyBody}
        onReplyBodyChange={setReplyBody}
        onSubmitReply={submitReply}
        isReplying={replyMutation.isPending}
        replyPlaceholder={m.account_feedback_reply_placeholder()}
        sendLabel={m.account_feedback_send()}
        onBack={back}
      />
    )
  }

  return (
    <div className="space-y-6">
      <PanelHeader
        title={m.account_feedback_title()}
        description={m.account_feedback_description()}
        unreadCount={unreadCount}
        markAllLabel={m.account_feedback_mark_all_read()}
        onMarkAllRead={() => markAllReadMutation.mutate()}
        isMarkingAllRead={markAllReadMutation.isPending}
      />

      {isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : hasError ? (
        <p className="text-sm text-[var(--color-destructive)]">{m.account_feedback_error()}</p>
      ) : items.length === 0 ? (
        <ListEmptyState
          title={m.account_feedback_empty_title()}
          description={m.account_feedback_empty_description()}
        />
      ) : (
        <div className="border-y border-gray-200 divide-y divide-gray-100">
          {items.map((item) => (
            <FeedbackFeedRow key={item.submission_id} item={item} locale={locale} onOpen={() => openItem(item)} />
          ))}
        </div>
      )}
    </div>
  )
}

/** Synthesize one chronological timeline: the report, the Klai resolution, and any thread replies. */
function buildFeedbackEntries(
  item: AccountFeedbackUpdate,
  thread: AccountPlatformMessageThreadDetail | null,
): ConversationEntry[] {
  const entries: ConversationEntry[] = [
    {
      id: `report-${item.submission_id}`,
      side: 'me',
      author: m.account_messages_you(),
      body: item.raw_text,
      at: item.created_at,
    },
  ]

  if (item.notification_body) {
    const at = item.item_updated_at ?? item.updated_at
    entries.push({
      id: `resolution-${item.submission_id}`,
      side: 'them',
      author: m.account_messages_platform_admin(),
      body: item.notification_body,
      at,
    })
    if ((item.item_status ?? item.submission_status) === 'resolved') {
      entries.push({ type: 'system', id: `resolved-${item.submission_id}`, label: m.account_feedback_marked_resolved(), at })
    }
  }

  if (thread) {
    for (const message of thread.messages) {
      entries.push({
        id: message.id,
        side: message.sender_type === 'user' ? 'me' : 'them',
        author: message.sender_type === 'user' ? m.account_messages_you() : m.account_messages_platform_admin(),
        body: message.body,
        at: message.created_at,
      })
    }
  }

  // Avoid double-printing the user's first reply if the backend later seeds the
  // report into the thread: de-dupe identical (side, body) pairs by keeping the
  // earliest occurrence.
  const seen = new Set<string>()
  return entries.filter((entry) => {
    if (entry.type === 'system') return true
    const key = `${entry.side}|${entry.body}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

// --- Shared conversation detail (back + header + timeline + composer) -------

function ConversationDetail({
  title,
  subtitle,
  badge,
  entries,
  loading,
  locale,
  replyBody,
  onReplyBodyChange,
  onSubmitReply,
  isReplying,
  replyPlaceholder,
  sendLabel,
  onBack,
}: {
  title: string
  subtitle?: string
  badge?: ReactNode
  entries: ConversationEntry[]
  loading: boolean
  locale: 'nl' | 'en'
  replyBody: string
  onReplyBodyChange: (value: string) => void
  onSubmitReply: () => void
  isReplying: boolean
  replyPlaceholder: string
  sendLabel: string
  onBack: () => void
}) {
  return (
    <ConversationPanel
      title={title}
      subtitle={subtitle}
      badge={badge}
      entries={entries}
      loading={loading}
      locale={locale}
      draft={replyBody}
      onDraftChange={onReplyBodyChange}
      onSend={onSubmitReply}
      isSending={isReplying}
      placeholder={replyPlaceholder}
      sendLabel={sendLabel}
      onBack={onBack}
    />
  )
}

// --- Feed rows -------------------------------------------------------------

function FeedRow({
  icon,
  unread,
  title,
  snippet,
  date,
  onOpen,
}: {
  icon: ReactNode
  unread: boolean
  title: string
  snippet: string
  date: string
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full gap-3 px-2 py-4 text-left klai-hover"
    >
      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {unread && <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-success)]" />}
          <h3 className="truncate text-sm font-medium text-gray-900">{title}</h3>
        </div>
        <p className="mt-1 line-clamp-1 text-sm text-gray-500">{snippet}</p>
        <p className="mt-1 text-xs text-gray-400">{date}</p>
      </div>
    </button>
  )
}

function FeedbackFeedRow({
  item,
  locale,
  onOpen,
}: {
  item: AccountFeedbackUpdate
  locale: 'nl' | 'en'
  onOpen: () => void
}) {
  const title = item.item_title || truncateText(item.raw_text, 88)
  const status = feedbackStatusLabel(item)

  return (
    <button type="button" onClick={onOpen} className="flex w-full gap-3 px-2 py-4 text-left klai-hover">
      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500">
        {item.source === 'assistant_problem' ? <Bug className="h-4 w-4" /> : <Lightbulb className="h-4 w-4" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            {item.unread && <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-success)]" />}
            <h3 className="truncate text-sm font-medium text-gray-900">{title}</h3>
          </div>
          <Badge variant={status.variant} className="shrink-0">
            {status.label}
          </Badge>
        </div>
        {item.notification_body ? (
          <p className="mt-1 line-clamp-1 text-sm text-gray-900">{item.notification_body}</p>
        ) : (
          <p className="mt-1 line-clamp-1 text-sm text-gray-500">{item.raw_text}</p>
        )}
        <p className="mt-1 text-xs text-gray-400">
          {m.account_feedback_updated()} {formatFeedbackDate(item.latest_update_at, locale)}
        </p>
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
