import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft,
  Trash2,
  Upload,
  Send,
  Plus,
  Loader2,
  ChevronDown,
  ChevronUp,
  X,
  History,
} from 'lucide-react'
import { useState, useRef, useEffect } from 'react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/focus/$notebookId')({
  component: NotebookDetailPage,
})

const FOCUS_BASE = '/research/v1'

// ── Types ─────────────────────────────────────────────────────────────────────

type SourceStatus = 'processing' | 'ready' | 'error'
type ChatMode = 'narrow' | 'broad' | 'web'
type AddTab = 'file' | 'url'

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
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: SourceStatus }) {
  if (status === 'ready')
    return <span className="text-xs text-emerald-600">{m.app_focus_source_status_ready()}</span>
  if (status === 'error')
    return <span className="text-xs text-red-500">{m.app_focus_source_status_error()}</span>
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
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // ── Notebook ────────────────────────────────────────────────────────────────

  const { data: notebook } = useQuery<Notebook>({
    queryKey: ['focus-notebook', notebookId, token],
    queryFn: async () => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Niet gevonden')
      return res.json()
    },
    enabled: !!token,
  })

  // ── Sources ─────────────────────────────────────────────────────────────────

  const { data: sources = [] } = useQuery<Source[]>({
    queryKey: ['focus-sources', notebookId, token],
    queryFn: async () => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/sources`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      const data = await res.json()
      return data.items ?? data
    },
    enabled: !!token,
    refetchInterval: (query) => {
      const data = query.state.data
      return Array.isArray(data) && data.some((s) => s.status === 'processing') ? 3000 : false
    },
  })

  const hasReadySources = sources.some((s) => s.status === 'ready')

  // ── Delete source ────────────────────────────────────────────────────────────

  const deleteSrcMutation = useMutation({
    mutationFn: async (srcId: string) => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/sources/${srcId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-sources', notebookId, token] })
    },
  })

  // ── Add source ───────────────────────────────────────────────────────────────

  const [showAdd, setShowAdd] = useState(false)
  const [addTab, setAddTab] = useState<AddTab>('file')
  const [urlInput, setUrlInput] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const addFileMutation = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/sources`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      })
      if (!res.ok) throw new Error('Uploaden mislukt')
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-sources', notebookId, token] })
      setShowAdd(false)
      setAddError(null)
    },
    onError: (err: Error) => setAddError(err.message),
  })

  const addUrlMutation = useMutation({
    mutationFn: async (url: string) => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/sources/url`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url }),
      })
      if (!res.ok) throw new Error('Toevoegen mislukt')
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-sources', notebookId, token] })
      setUrlInput('')
      setShowAdd(false)
      setAddError(null)
    },
    onError: (err: Error) => setAddError(err.message),
  })

  // ── History ──────────────────────────────────────────────────────────────────

  const { data: historyData } = useQuery<{ items: HistoryMessage[] }>({
    queryKey: ['focus-history', notebookId, token],
    queryFn: async () => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/history`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Geschiedenis ophalen mislukt')
      return res.json()
    },
    enabled: !!token && notebook?.save_history === true,
  })

  const toggleHistoryMutation = useMutation({
    mutationFn: async (saveHistory: boolean) => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ save_history: saveHistory }),
      })
      if (!res.ok) throw new Error('Instelling opslaan mislukt')
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-notebook', notebookId, token] })
    },
  })

  const clearHistoryMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/history`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Wissen mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-history', notebookId, token] })
      setMessages([])
    },
  })

  // ── Chat ─────────────────────────────────────────────────────────────────────

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatMode, setChatMode] = useState<ChatMode>('narrow')
  const [streaming, setStreaming] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [expandedCitations, setExpandedCitations] = useState<Set<number>>(new Set())
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (notebook) setChatMode(notebook.default_mode as ChatMode)
  }, [notebook?.default_mode])

  // Load persisted history into messages state once
  useEffect(() => {
    if (historyData?.items && historyData.items.length > 0 && messages.length === 0) {
      setMessages(
        historyData.items.map((msg) => ({ role: msg.role, content: msg.content }))
      )
    }
  }, [historyData])

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
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/chat`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
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
    } catch {
      setChatError(m.app_focus_chat_error())
      setMessages((prev) => prev.filter((_, i) => i !== prev.length - 1))
    } finally {
      setStreaming(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <div className="p-8 space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
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

      {/* Two-column layout */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_3fr]">
        {/* Sources panel */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">{m.app_focus_sources_heading()}</CardTitle>
                <button
                  onClick={() => {
                    setShowAdd((v) => !v)
                    setAddError(null)
                  }}
                  className="flex items-center gap-1 text-xs font-medium text-[var(--color-purple-accent)] hover:text-[var(--color-purple-deep)] transition-colors"
                >
                  {showAdd ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
                  {showAdd ? m.app_focus_create_cancel() : m.app_focus_add_source()}
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
                        <p className="truncate text-xs font-medium text-[var(--color-purple-deep)]">
                          {src.name}
                        </p>
                        <StatusBadge status={src.status} />
                      </div>
                      <button
                        onClick={() => deleteSrcMutation.mutate(src.id)}
                        disabled={
                          deleteSrcMutation.isPending && deleteSrcMutation.variables === src.id
                        }
                        className="shrink-0 p-1 text-[var(--color-muted-foreground)] transition-colors hover:text-red-500 disabled:opacity-50"
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

          {/* Add source inline */}
          {showAdd && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">{m.app_focus_add_source()}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {/* Tabs */}
                <div className="flex gap-1 p-1 bg-[var(--color-muted)]/40 rounded-lg w-fit">
                  {(['file', 'url'] as AddTab[]).map((tab) => (
                    <button
                      key={tab}
                      onClick={() => {
                        setAddTab(tab)
                        setAddError(null)
                      }}
                      className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                        addTab === tab
                          ? 'bg-white shadow-sm text-[var(--color-purple-deep)]'
                          : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                      }`}
                    >
                      {tab === 'file' ? m.app_focus_source_add_tab_file() : 'URL / YouTube'}
                    </button>
                  ))}
                </div>

                {addTab === 'file' && (
                  <div
                    className={`cursor-pointer rounded-lg border-2 border-dashed p-4 text-center transition-colors ${
                      dragging
                        ? 'border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5'
                        : 'border-[var(--color-border)] hover:border-[var(--color-purple-accent)]/50'
                    }`}
                    onClick={() => fileInputRef.current?.click()}
                    onDragOver={(e) => {
                      e.preventDefault()
                      setDragging(true)
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(e) => {
                      e.preventDefault()
                      setDragging(false)
                      const f = e.dataTransfer.files[0]
                      if (f) addFileMutation.mutate(f)
                    }}
                  >
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".pdf,.docx,.txt,.md"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0]
                        if (f) addFileMutation.mutate(f)
                      }}
                    />
                    {addFileMutation.isPending ? (
                      <div className="flex items-center justify-center gap-2">
                        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                        <span className="text-xs text-[var(--color-muted-foreground)]">
                          {m.app_focus_source_uploading()}
                        </span>
                      </div>
                    ) : (
                      <div>
                        <Upload className="mx-auto mb-1.5 h-5 w-5 text-[var(--color-muted-foreground)]" />
                        <p className="text-xs font-medium">{m.app_focus_source_file_hint()}</p>
                        <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
                          PDF, DOCX, TXT, MD
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {addTab === 'url' && (
                  <div className="flex gap-2">
                    <input
                      type="url"
                      value={urlInput}
                      onChange={(e) => setUrlInput(e.target.value)}
                      placeholder={m.app_focus_source_url_placeholder()}
                      className="flex-1 rounded-md border border-[var(--color-border)] bg-transparent px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--color-purple-accent)]"
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && urlInput.trim())
                          addUrlMutation.mutate(urlInput.trim())
                      }}
                    />
                    <Button
                      size="sm"
                      onClick={() => addUrlMutation.mutate(urlInput.trim())}
                      disabled={!urlInput.trim() || addUrlMutation.isPending}
                    >
                      {addUrlMutation.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        m.app_focus_source_add_button()
                      )}
                    </Button>
                  </div>
                )}

                {addError && <p className="text-xs text-red-600">{addError}</p>}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Chat panel */}
        <Card className="flex flex-col" style={{ minHeight: '560px' }}>
          <CardHeader className="shrink-0 pb-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <CardTitle className="text-sm">{m.app_focus_chat_heading()}</CardTitle>
                {messages.length > 0 && (
                  <button
                    onClick={() => clearHistoryMutation.mutate()}
                    disabled={clearHistoryMutation.isPending}
                    className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
                  >
                    {m.app_focus_chat_new_session()}
                  </button>
                )}
              </div>
              <div className="flex items-center gap-1">
                {/* History toggle */}
                <button
                  onClick={() => toggleHistoryMutation.mutate(!(notebook?.save_history ?? true))}
                  disabled={toggleHistoryMutation.isPending}
                  title={
                    notebook?.save_history
                      ? m.app_focus_chat_history_on_tooltip()
                      : m.app_focus_chat_history_off_tooltip()
                  }
                  className={`rounded-md p-1 transition-colors ${
                    notebook?.save_history
                      ? 'text-[var(--color-purple-deep)]'
                      : 'text-[var(--color-muted-foreground)]'
                  } hover:text-[var(--color-foreground)]`}
                >
                  <History className="h-3.5 w-3.5" />
                </button>
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
                          ? 'bg-white shadow-sm text-[var(--color-purple-deep)]'
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
                        ? 'bg-[var(--color-purple-accent)] text-white'
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
                              {c.source_name}
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
              <p className="text-center text-xs text-red-600">{chatError}</p>
            )}
            <div ref={messagesEndRef} />
          </CardContent>

          {/* Input */}
          <div className="shrink-0 border-t border-[var(--color-border)] p-4 pt-3">
            <div className="flex gap-2">
              <input
                type="text"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder={
                  hasReadySources
                    ? m.app_focus_chat_placeholder()
                    : m.app_focus_chat_disabled_hint()
                }
                disabled={!hasReadySources || streaming}
                className="flex-1 rounded-lg border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--color-purple-accent)] disabled:cursor-not-allowed disabled:opacity-50"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') sendMessage()
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
          </div>
        </Card>
      </div>
    </div>
  )
}
