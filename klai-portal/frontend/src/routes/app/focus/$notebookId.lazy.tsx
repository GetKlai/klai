import { createLazyFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth, readCsrfCookie } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tooltip } from '@/components/ui/tooltip'
import {
  ArrowLeft,
  Trash2,
  Send,
  Plus,
  Loader2,
  ChevronDown,
  ChevronUp,
  X,
  History,
  Info,
  RotateCcw,
} from 'lucide-react'
import { focusLogger } from '@/lib/logger'
import { useState, useRef, useEffect } from 'react'
import * as m from '@/paraglide/messages'
import { SaveToKnowledgeModal } from '@/components/knowledge/SaveToKnowledgeModal'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createLazyFileRoute('/app/focus/$notebookId')({
  component: () => (
    <ProductGuard product="chat">
      <NotebookDetailPage />
    </ProductGuard>
  ),
})

const FOCUS_BASE = '/api/research/v1'

// ── Types ─────────────────────────────────────────────────────────────────────

type SourceStatus = 'processing' | 'ready' | 'error'
type ChatMode = 'narrow' | 'broad' | 'web'
interface Source {
  id: string
  name: string
  type: string
  status: SourceStatus
  error_message: string | null
  chunks_count: number
}

interface Notebook {
  id: string
  name: string
  description: string | null
  scope: string
  default_mode: string
  save_history: boolean
  sources_count: number
}

interface HistoryMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

interface Citation {
  source_id: string
  source_name: string
  page: number | null
  url?: string | null
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: SourceStatus }) {
  if (status === 'ready')
    return <span className="text-xs text-[var(--color-success)]">{m.app_focus_source_status_ready()}</span>
  if (status === 'error')
    return <span className="text-xs text-[var(--color-destructive)]">{m.app_focus_source_status_error()}</span>
  return (
    <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
      <Loader2 className="h-3 w-3 animate-spin" />
      {m.app_focus_source_status_processing()}
    </span>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

function NotebookDetailPage() {
  const { notebookId } = Route.useParams()
  const auth = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // ── Onboarding ───────────────────────────────────────────────────────────────

  const [showOnboarding, setShowOnboarding] = useState(() => {
    try {
      return localStorage.getItem('focus_onboarding_dismissed') !== '1'
    } catch {
      return true
    }
  })

  function dismissOnboarding() {
    try {
      localStorage.setItem('focus_onboarding_dismissed', '1')
    } catch { /* localStorage unavailable in sandboxed contexts */ }
    setShowOnboarding(false)
  }

  // ── Notebook ────────────────────────────────────────────────────────────────

  const { data: notebook } = useQuery<Notebook>({
    queryKey: ['focus-notebook', notebookId],
    queryFn: async () => apiFetch<Notebook>(`${FOCUS_BASE}/notebooks/${notebookId}`),
    enabled: auth.isAuthenticated,
  })

  // ── Sources ─────────────────────────────────────────────────────────────────

  const { data: sources = [] } = useQuery<Source[]>({
    queryKey: ['focus-sources', notebookId],
    queryFn: async () => {
      const data = await apiFetch<{ items?: Source[] } | Source[]>(`${FOCUS_BASE}/notebooks/${notebookId}/sources`)
      return (data as { items?: Source[] }).items ?? (data as Source[])
    },
    enabled: auth.isAuthenticated,
    refetchInterval: (query) => {
      const data = query.state.data
      return Array.isArray(data) && data.some((s) => s.status === 'processing') ? 3000 : false
    },
  })

  const hasReadySources = sources.some((s) => s.status === 'ready')

  // ── Delete source ────────────────────────────────────────────────────────────

  const deleteSrcMutation = useMutation({
    mutationFn: async (srcId: string) => {
      await apiFetch(`${FOCUS_BASE}/notebooks/${notebookId}/sources/${srcId}`, { method: 'DELETE' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['focus-sources', notebookId] })
    },
  })

  // ── History ──────────────────────────────────────────────────────────────────

  const { data: historyData } = useQuery<{ items: HistoryMessage[] }>({
    queryKey: ['focus-history', notebookId],
    queryFn: async () => apiFetch<{ items: HistoryMessage[] }>(`${FOCUS_BASE}/notebooks/${notebookId}/history`),
    enabled: auth.isAuthenticated && notebook?.save_history === true,
  })

  const toggleHistoryMutation = useMutation({
    mutationFn: async (saveHistory: boolean) => {
      return apiFetch(`${FOCUS_BASE}/notebooks/${notebookId}`, {
        method: 'PATCH',
        body: JSON.stringify({ save_history: saveHistory }),
      })
    },
    onSuccess: () => {
      // Only refreshes notebook metadata — does NOT reset the current chat session
      void queryClient.invalidateQueries({ queryKey: ['focus-notebook', notebookId] })
    },
  })

  const clearHistoryMutation = useMutation({
    mutationFn: async () => {
      await apiFetch(`${FOCUS_BASE}/notebooks/${notebookId}/history`, { method: 'DELETE' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['focus-history', notebookId] })
      setMessages([])
    },
  })

  // ── Chat ─────────────────────────────────────────────────────────────────────

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [showSaveModal, setShowSaveModal] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatMode, setChatMode] = useState<ChatMode>('narrow')
  const [streaming, setStreaming] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [expandedCitations, setExpandedCitations] = useState<Set<number>>(new Set())
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (notebook) setChatMode(notebook.default_mode as ChatMode)
  }, [notebook])

  // Load persisted history into messages state once
  useEffect(() => {
    if (historyData?.items && historyData.items.length > 0 && messages.length === 0) {
      setMessages(
        historyData.items.map((msg) => ({ role: msg.role, content: msg.content }))
      )
    }
  }, [historyData, messages.length])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!chatInput.trim() || streaming) return
    const question = chatInput.trim()
    setChatInput('')
    setChatError(null)
    setMessages((prev) => [...prev, { role: 'user', content: question }])
    setStreaming(true)

    let assistantContent = ''
    let citations: Citation[] = []

    try {
      const csrf = readCsrfCookie()
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (csrf) headers['X-CSRF-Token'] = csrf
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/chat`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify({
          question: question,
          mode: chatMode,
          history: messages.slice(-8).map((msg) => ({ role: msg.role, content: msg.content })),
        }),
      })
      if (!res.ok) throw new Error('Chat mislukt')
      if (!res.body) throw new Error('Geen response')

      setMessages((prev) => [...prev, { role: 'assistant', content: '' }])

      const reader = res.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = decoder.decode(value)
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue
          let event: { type: string; content?: string; citations?: Citation[] }
          try {
            event = JSON.parse(line.slice(6))
          } catch {
            continue // ignore malformed SSE lines
          }
          if (event.type === 'token') {
            assistantContent += event.content ?? ''
            setMessages((prev) => {
              const next = [...prev]
              next[next.length - 1] = { role: 'assistant', content: assistantContent }
              return next
            })
          } else if (event.type === 'done') {
            citations = event.citations ?? []
            setMessages((prev) => {
              const next = [...prev]
              next[next.length - 1] = { role: 'assistant', content: assistantContent, citations }
              return next
            })
          } else if (event.type === 'error') {
            throw new Error(event.content)
          }
        }
      }
    } catch (err) {
      focusLogger.error('Chat message failed', { notebookId, mode: chatMode, err })
      setChatError(m.app_focus_chat_error())
      setMessages((prev) => prev.filter((_, i) => i !== prev.length - 1))
    } finally {
      setStreaming(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-start justify-between mb-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {notebook?.name ?? m.app_focus_loading()}
          </h1>
          <Button type="button" variant="ghost" size="sm" onClick={() => navigate({ to: '/app/focus' })}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.app_focus_back()}
          </Button>
        </div>
        {notebook?.description && (
          <p className="text-sm text-[var(--color-muted-foreground)]">{notebook.description}</p>
        )}
      </div>

      {/* Onboarding banner */}
      {showOnboarding && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-secondary)] p-4">
          <div className="flex items-start gap-3">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-rl-accent)]" />
            <div className="flex-1 space-y-3 text-sm">
              <p className="font-medium text-[var(--color-foreground)]">
                {m.app_focus_onboarding_title()}
              </p>
              <p className="text-[var(--color-muted-foreground)]">
                {m.app_focus_onboarding_body()}
              </p>
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-[var(--color-foreground)]">
                  {m.app_focus_onboarding_modes_heading()}
                </p>
                <ul className="space-y-0.5 text-xs text-[var(--color-muted-foreground)]">
                  <li>
                    <span className="font-medium text-[var(--color-foreground)]">
                      {m.app_focus_chat_mode_narrow()}
                    </span>{' '}
                    &ndash; {m.app_focus_chat_mode_narrow_tooltip()}
                  </li>
                  <li>
                    <span className="font-medium text-[var(--color-foreground)]">
                      {m.app_focus_chat_mode_broad()}
                    </span>{' '}
                    &ndash; {m.app_focus_chat_mode_broad_tooltip()}
                  </li>
                  <li>
                    <span className="font-medium text-[var(--color-foreground)]">
                      {m.app_focus_chat_mode_web()}
                    </span>{' '}
                    &ndash; {m.app_focus_chat_mode_web_tooltip()}
                  </li>
                </ul>
              </div>
              <div className="space-y-0.5">
                <p className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-foreground)]">
                  <History className="h-3.5 w-3.5" />
                  {m.app_focus_onboarding_history_heading()}
                </p>
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  {m.app_focus_onboarding_history_body()}
                </p>
              </div>
              <button
                onClick={dismissOnboarding}
                className="text-xs font-medium text-[var(--color-rl-accent)] hover:text-[var(--color-foreground)] transition-colors"
              >
                {m.app_focus_onboarding_dismiss()}
              </button>
            </div>
            <button
              onClick={dismissOnboarding}
              className="shrink-0 rounded p-1 text-[var(--color-muted-foreground)] hover:bg-[var(--color-border)] hover:text-[var(--color-foreground)] transition-colors"
              aria-label={m.app_focus_onboarding_dismiss()}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Two-column layout */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_3fr]">
        {/* Sources panel */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">{m.app_focus_sources_heading()}</CardTitle>
                <button
                  onClick={() => navigate({ to: '/app/focus/$notebookId/add-source', params: { notebookId } })}
                  className="flex items-center gap-1 text-xs font-medium text-[var(--color-rl-accent)] hover:text-[var(--color-foreground)] transition-colors"
                >
                  <Plus className="h-3.5 w-3.5" />
                  {m.app_focus_add_source()}
                </button>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              {sources.length === 0 ? (
                <p className="py-3 text-xs text-[var(--color-muted-foreground)]">
                  {m.app_focus_no_sources()}
                </p>
              ) : (
                <ul className="space-y-0">
                  {sources.map((src) => (
                    <li
                      key={src.id}
                      className="flex items-center justify-between gap-2 border-b border-[var(--color-border)] py-2 last:border-0"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium text-[var(--color-foreground)]">
                          {src.name}
                        </p>
                        <StatusBadge status={src.status} />
                      </div>
                      <button
                        onClick={() => deleteSrcMutation.mutate(src.id)}
                        disabled={
                          deleteSrcMutation.isPending && deleteSrcMutation.variables === src.id
                        }
                        className="shrink-0 p-1 text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-destructive)] disabled:opacity-50"
                        aria-label={m.app_focus_source_delete()}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

        </div>

        {/* Chat panel */}
        <Card className="flex flex-col" style={{ minHeight: '560px' }}>
          <CardHeader className="shrink-0 pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">{m.app_focus_chat_heading()}</CardTitle>
              <div className="flex items-center gap-2">
                {/* Clear session */}
                {messages.length > 0 && (
                  <Tooltip label={m.app_focus_chat_new_session()}>
                    <button
                      onClick={() => clearHistoryMutation.mutate()}
                      disabled={clearHistoryMutation.isPending}
                      className="rounded-md p-1 text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                    </button>
                  </Tooltip>
                )}
                {/* History toggle */}
                <div className="flex gap-1 rounded-lg p-1 bg-[var(--color-muted)]/40">
                  <button
                    onClick={() => {
                      if (notebook?.save_history !== true) toggleHistoryMutation.mutate(true)
                    }}
                    disabled={toggleHistoryMutation.isPending}
                    title={m.app_focus_chat_history_on_tooltip()}
                    className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                      notebook?.save_history !== false
                        ? 'bg-[var(--color-background)] shadow-sm text-[var(--color-foreground)]'
                        : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                    }`}
                  >
                    <History className="h-3 w-3" />
                    {m.app_focus_chat_history_on()}
                  </button>
                  <button
                    onClick={() => {
                      if (notebook?.save_history !== false) toggleHistoryMutation.mutate(false)
                    }}
                    disabled={toggleHistoryMutation.isPending}
                    title={m.app_focus_chat_history_off_tooltip()}
                    className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                      notebook?.save_history === false
                        ? 'bg-[var(--color-background)] shadow-sm text-[var(--color-foreground)]'
                        : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                    }`}
                  >
                    {m.app_focus_chat_history_off()}
                  </button>
                </div>
                {/* Mode selector */}
                <div className="flex gap-1 rounded-lg p-1 bg-[var(--color-muted)]/40">
                  {(['narrow', 'broad', 'web'] as ChatMode[]).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setChatMode(mode)}
                      title={
                        mode === 'narrow'
                          ? m.app_focus_chat_mode_narrow_tooltip()
                          : mode === 'broad'
                            ? m.app_focus_chat_mode_broad_tooltip()
                            : m.app_focus_chat_mode_web_tooltip()
                      }
                      className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                        chatMode === mode
                          ? 'bg-[var(--color-background)] shadow-sm text-[var(--color-foreground)]'
                          : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                      }`}
                    >
                      {mode === 'narrow'
                        ? m.app_focus_chat_mode_narrow()
                        : mode === 'broad'
                          ? m.app_focus_chat_mode_broad()
                          : m.app_focus_chat_mode_web()}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </CardHeader>

          {/* Messages */}
          <CardContent className="flex-1 overflow-y-auto space-y-4 pb-0">
            {messages.length === 0 ? (
              <p className="py-8 text-center text-xs text-[var(--color-muted-foreground)]">
                {hasReadySources
                  ? m.app_focus_chat_placeholder()
                  : m.app_focus_chat_disabled_hint()}
              </p>
            ) : (
              messages.map((msg, i) => (
                <div
                  key={i}
                  className={`space-y-1 ${msg.role === 'user' ? 'text-right' : 'text-left'}`}
                >
                  <div
                    className={`inline-block max-w-[85%] rounded-xl px-3 py-2 text-sm ${
                      msg.role === 'user'
                        ? 'bg-[var(--color-rl-accent)] text-white'
                        : 'bg-[var(--color-muted)]/40 text-[var(--color-foreground)]'
                    }`}
                  >
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                    {msg.role === 'assistant' && msg.content === '' && (
                      <span className="inline-block h-3 w-1 animate-pulse bg-current" />
                    )}
                  </div>
                  {msg.citations && msg.citations.length > 0 && (
                    <div className="text-left">
                      <button
                        onClick={() =>
                          setExpandedCitations((prev) => {
                            const next = new Set(prev)
                            if (next.has(i)) next.delete(i)
                            else next.add(i)
                            return next
                          })
                        }
                        className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-foreground)]"
                      >
                        {expandedCitations.has(i) ? (
                          <ChevronUp className="h-3 w-3" />
                        ) : (
                          <ChevronDown className="h-3 w-3" />
                        )}
                        {msg.citations.length} {m.app_focus_chat_sources_label()}
                      </button>
                      {expandedCitations.has(i) && (
                        <div className="mt-1 space-y-0.5 border-l-2 border-[var(--color-border)] pl-2">
                          {msg.citations.map((c, j) => (
                            <p key={j} className="text-xs text-[var(--color-muted-foreground)]">
                              {c.url ? (
                                <a
                                  href={c.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-[var(--color-rl-accent-dark)] hover:text-[var(--color-foreground)] underline"
                                >
                                  {c.source_name}
                                </a>
                              ) : (
                                c.source_name
                              )}
                              {c.page ? `, p. ${c.page}` : ''}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))
            )}
            {chatError && (
              <p className="text-center text-xs text-[var(--color-destructive)]">{chatError}</p>
            )}
            <div ref={messagesEndRef} />
          </CardContent>

          {/* Input */}
          <div className="shrink-0 border-t border-[var(--color-border)] p-4 pt-3">
            <div className="flex gap-2">
              <Input
                type="text"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder={
                  hasReadySources
                    ? m.app_focus_chat_placeholder()
                    : m.app_focus_chat_disabled_hint()
                }
                disabled={!hasReadySources || streaming}
                className="flex-1"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void sendMessage()
                }}
              />
              <Button
                size="sm"
                onClick={sendMessage}
                disabled={!hasReadySources || streaming || !chatInput.trim()}
              >
                {streaming ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </Button>
            </div>
            {messages.some((msg) => msg.role === 'assistant') && (
              <div className="mt-2 flex justify-end">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-xs h-7"
                  onClick={() => setShowSaveModal(true)}
                >
                  {m.knowledge_save_button()}
                </Button>
              </div>
            )}
          </div>
        </Card>
      </div>
      {showSaveModal && (
        <SaveToKnowledgeModal
          initialContent={[...messages].reverse().find((msg) => msg.role === 'assistant')?.content ?? ''}
          initialTitle={[...messages].reverse().find((msg) => msg.role === 'assistant')?.content?.split(/[.!?]/)[0]?.slice(0, 80) ?? ''}
          onClose={() => setShowSaveModal(false)}
          onSuccess={() => setShowSaveModal(false)}
        />
      )}
    </div>
  )
}
