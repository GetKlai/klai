import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bug, Download, Lightbulb, Loader2, MessageSquare, Send, Settings, SlidersHorizontal } from 'lucide-react'
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

  const tabs: TabItem<TabId>[] = [
    { id: 'settings', label: m.account_tab_settings(), icon: Settings },
    {
      id: 'messages',
      label: m.account_tab_messages(),
      icon: MessageSquare,
      count: messageUnreadCount,
      countLabel: m.account_messages_unread(),
    },
    {
      id: 'feedback',
      label: m.account_tab_feedback(),
      icon: MessageSquare,
      count: feedbackUnreadCount,
      countLabel: m.account_feedback_unread(),
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
          onMarkRead={(notificationId) => markFeedbackReadMutation.mutate(notificationId)}
          onMarkAllRead={() => markAllFeedbackReadMutation.mutate()}
          markingReadId={markFeedbackReadMutation.variables}
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
  onSelect,
  onReply,
  isReplying,
}: {
  items: AccountPlatformMessageThread[]
  detail: AccountPlatformMessageThreadDetail | null
  selectedThreadId: number | null
  isLoading: boolean
  detailLoading: boolean
  error: unknown
  locale: 'nl' | 'en'
  onSelect: (thread: AccountPlatformMessageThread) => void
  onReply: (body: string) => void
  isReplying: boolean
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
      <div>
        <h2 className="text-sm font-medium text-gray-900 mb-2">{m.account_messages_title()}</h2>
        <p className="text-sm text-gray-400">{m.account_messages_description()}</p>
      </div>

      {isLoading && (
        <div className="border-y border-gray-200 divide-y divide-gray-100">
          {[0, 1, 2].map((index) => (
            <div key={index} className="flex gap-3 py-4">
              <div className="mt-0.5 h-8 w-8 shrink-0 rounded-full bg-gray-100" />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="h-4 w-2/3 rounded bg-gray-100" />
                <div className="h-3 w-full rounded bg-gray-100" />
              </div>
            </div>
          ))}
        </div>
      )}

      {!isLoading && hasError && (
        <p className="text-sm text-[var(--color-destructive)]">{m.account_messages_error()}</p>
      )}

      {!isLoading && !hasError && items.length === 0 && (
        <div className="border-y border-gray-200 py-8">
          <p className="text-sm font-medium text-gray-900">{m.account_messages_empty_title()}</p>
          <p className="mt-1 text-sm text-gray-400">{m.account_messages_empty_description()}</p>
        </div>
      )}

      {!isLoading && !hasError && items.length > 0 && (
        <div className="border-y border-gray-200 divide-y divide-gray-100">
          {items.map((thread) => (
            <button
              key={thread.id}
              type="button"
              onClick={() => onSelect(thread)}
              className="flex w-full gap-3 py-4 text-left klai-hover"
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
          ))}
        </div>
      )}

      {selectedThreadId !== null && (
        <section className="space-y-4 border-t border-gray-200 pt-6">
          {detailLoading || !detail ? (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              {m.admin_shared_loading()}
            </div>
          ) : (
            <>
              <div>
                <h3 className="text-base font-display-bold text-gray-900">{detail.thread.subject}</h3>
                <p className="mt-1 text-xs text-gray-400">
                  {m.account_messages_started()} {formatFeedbackDate(detail.thread.created_at, locale)}
                </p>
              </div>
              <div className="divide-y divide-gray-100 border-y border-gray-200">
                {detail.messages.map((message) => (
                  <article key={message.id} className="py-4">
                    <p className="mb-1 text-xs text-gray-400">
                      {message.sender_type === 'user'
                        ? m.account_messages_you()
                        : m.account_messages_platform_admin()}{' '}
                      · {formatFeedbackDate(message.created_at, locale)}
                    </p>
                    <p className="whitespace-pre-wrap text-sm leading-6 text-gray-900">{message.body}</p>
                  </article>
                ))}
              </div>
              <div className="space-y-2">
                <Label htmlFor="account-message-reply">{m.account_messages_reply()}</Label>
                <Textarea
                  id="account-message-reply"
                  rows={4}
                  value={replyBody}
                  maxLength={4000}
                  onChange={(event) => setReplyBody(event.target.value)}
                />
                <Button
                  type="button"
                  disabled={replyBody.trim().length === 0 || isReplying}
                  onClick={submitReply}
                >
                  {isReplying ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                  {m.account_messages_send()}
                </Button>
              </div>
            </>
          )}
        </section>
      )}
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
  markingReadId,
  isMarkingAllRead,
}: {
  items: AccountFeedbackUpdate[]
  isLoading: boolean
  error: unknown
  locale: 'nl' | 'en'
  unreadCount: number
  onMarkRead: (notificationId: number) => void
  onMarkAllRead: () => void
  markingReadId?: number
  isMarkingAllRead: boolean
}) {
  const hasError = error != null

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-medium text-gray-900 mb-2">{m.account_feedback_title()}</h2>
          <p className="text-sm text-gray-400">{m.account_feedback_description()}</p>
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
            {m.account_feedback_mark_all_read()}
          </Button>
        )}
      </div>

      {isLoading && (
        <div className="border-y border-gray-200 divide-y divide-gray-100">
          {[0, 1, 2].map((index) => (
            <div key={index} className="flex gap-3 py-4">
              <div className="mt-0.5 h-8 w-8 shrink-0 rounded-full bg-gray-100" />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="h-4 w-2/3 rounded bg-gray-100" />
                <div className="h-3 w-full rounded bg-gray-100" />
                <div className="h-3 w-1/3 rounded bg-gray-100" />
              </div>
            </div>
          ))}
        </div>
      )}

      {!isLoading && hasError && (
        <p className="text-sm text-[var(--color-destructive)]">{m.account_feedback_error()}</p>
      )}

      {!isLoading && !hasError && items.length === 0 && (
        <div className="border-y border-gray-200 py-8">
          <p className="text-sm font-medium text-gray-900">{m.account_feedback_empty_title()}</p>
          <p className="mt-1 text-sm text-gray-400">{m.account_feedback_empty_description()}</p>
        </div>
      )}

      {!isLoading && !hasError && items.length > 0 && (
        <div className="border-y border-gray-200 divide-y divide-gray-100">
          {items.map((item) => (
            <FeedbackUpdateRow
              key={item.submission_id}
              item={item}
              locale={locale}
              onMarkRead={onMarkRead}
              isMarkingRead={markingReadId === item.notification_id}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function FeedbackUpdateRow({
  item,
  locale,
  onMarkRead,
  isMarkingRead,
}: {
  item: AccountFeedbackUpdate
  locale: 'nl' | 'en'
  onMarkRead: (notificationId: number) => void
  isMarkingRead: boolean
}) {
  const title = item.item_title || truncateText(item.raw_text, 88)
  const status = feedbackStatusLabel(item)
  const updatedAt = formatFeedbackDate(item.latest_update_at, locale)
  const createdAt = formatFeedbackDate(item.created_at, locale)
  const notificationId = item.notification_id ?? null

  return (
    <article className="flex gap-3 py-4">
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
        {item.unread && notificationId !== null && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-3"
            onClick={() => onMarkRead(notificationId)}
            disabled={isMarkingRead}
          >
            {m.account_feedback_mark_read()}
          </Button>
        )}
      </div>
    </article>
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
