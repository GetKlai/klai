import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useState, useEffect, useRef } from 'react'
import { BookOpen, Plus, Trash2, Upload, Link, Send, ChevronDown, ChevronUp } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/research')({
  component: ResearchPage,
})

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

// ── Types ───────────────────────────────────────────────────────────────────

interface Notebook {
  id: string
  name: string
  description: string | null
  scope: string
  default_mode: string
  sources_count: number
  created_at: string
}

interface Source {
  id: string
  name: string
  type: string
  status: string
  chunks_count: number | null
  error_message: string | null
  created_at: string
}

interface Citation {
  source_id: string
  source_name: string
  page: number | null
  excerpt: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  mode?: string
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function statusLabel(status: string) {
  if (status === 'ready') return m.app_research_source_status_ready()
  if (status === 'processing' || status === 'pending') return m.app_research_source_status_processing()
  return m.app_research_source_status_error()
}

function statusColor(status: string) {
  if (status === 'ready') return 'text-green-600'
  if (status === 'processing' || status === 'pending') return 'text-amber-500'
  return 'text-red-500'
}

// ── Sub-components ────────────────────────────────────────────────────────────

function CreateNotebookModal({
  token,
  onCreated,
  onClose,
}: {
  token: string
  onCreated: (nb: Notebook) => void
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [scope, setScope] = useState('personal')
  const [mode, setMode] = useState('narrow')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    const resp = await fetch(`${API_BASE}/research/v1/notebooks`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: description || undefined, scope, default_mode: mode }),
    })
    setLoading(false)
    if (resp.ok) {
      const nb = await resp.json()
      onCreated(nb)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-[var(--color-card)] rounded-xl p-6 w-full max-w-md shadow-xl">
        <h2 className="font-serif text-lg font-bold text-[var(--color-purple-deep)] mb-4">
          {m.app_research_new_notebook()}
        </h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-xs font-medium text-[var(--color-foreground)]">
              {m.app_research_notebook_name_label()}
            </label>
            <input
              className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="text-xs font-medium text-[var(--color-foreground)]">
              {m.app_research_notebook_description_label()}
            </label>
            <input
              className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="flex gap-4">
            <div className="flex-1">
              <label className="text-xs font-medium text-[var(--color-foreground)]">
                {m.app_research_notebook_scope_label()}
              </label>
              <select
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              >
                <option value="personal">{m.app_research_notebook_scope_personal()}</option>
                <option value="org">{m.app_research_notebook_scope_org()}</option>
              </select>
            </div>
            <div className="flex-1">
              <label className="text-xs font-medium text-[var(--color-foreground)]">
                {m.app_research_notebook_mode_label()}
              </label>
              <select
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
                value={mode}
                onChange={(e) => setMode(e.target.value)}
              >
                <option value="narrow">{m.app_research_notebook_mode_narrow()}</option>
                <option value="broad">{m.app_research_notebook_mode_broad()}</option>
                <option value="web">{m.app_research_notebook_mode_web()}</option>
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-4 py-2 text-sm text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]"
            >
              {m.app_research_create_cancel()}
            </button>
            <button
              type="submit"
              disabled={loading || !name.trim()}
              className="rounded-md bg-[var(--color-purple-accent)] px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {loading ? '…' : m.app_research_create_submit()}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function NotebookDetail({
  notebook,
  token,
  onBack,
}: {
  notebook: Notebook
  token: string
  onBack: () => void
}) {
  const [sources, setSources] = useState<Source[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState(notebook.default_mode)
  const [streaming, setStreaming] = useState(false)
  const [showUrlInput, setShowUrlInput] = useState(false)
  const [urlValue, setUrlValue] = useState('')
  const chatEndRef = useRef<HTMLDivElement>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const hasReadySources = sources.some((s) => s.status === 'ready')

  async function loadSources() {
    const resp = await fetch(`${API_BASE}/research/v1/notebooks/${notebook.id}/sources`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (resp.ok) {
      const data = await resp.json()
      setSources(data.items)
    }
  }

  useEffect(() => {
    loadSources()
    // Poll sources while any are processing
    pollRef.current = setInterval(() => {
      setSources((prev) => {
        const needsPoll = prev.some((s) => s.status === 'pending' || s.status === 'processing')
        if (needsPoll) loadSources()
        return prev
      })
    }, 3000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [notebook.id])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const fd = new FormData()
    fd.append('file', file)
    await fetch(`${API_BASE}/research/v1/notebooks/${notebook.id}/sources`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: fd,
    })
    await loadSources()
    e.target.value = ''
  }

  async function handleUrlSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!urlValue.trim()) return
    const isYoutube = urlValue.includes('youtube.com') || urlValue.includes('youtu.be')
    await fetch(`${API_BASE}/research/v1/notebooks/${notebook.id}/sources/url`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: isYoutube ? 'youtube' : 'url', url: urlValue }),
    })
    setUrlValue('')
    setShowUrlInput(false)
    await loadSources()
  }

  async function handleDeleteSource(srcId: string) {
    await fetch(`${API_BASE}/research/v1/notebooks/${notebook.id}/sources/${srcId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    })
    setSources((prev) => prev.filter((s) => s.id !== srcId))
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault()
    if (!question.trim() || streaming) return

    const userMsg: ChatMessage = { role: 'user', content: question }
    const assistantMsg: ChatMessage = { role: 'assistant', content: '', mode }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setQuestion('')
    setStreaming(true)

    const history = messages.map((msg) => ({ role: msg.role, content: msg.content }))

    const resp = await fetch(`${API_BASE}/research/v1/notebooks/${notebook.id}/chat`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, mode, history }),
    })

    if (!resp.ok || !resp.body) {
      setMessages((prev) => {
        const updated = [...prev]
        updated[updated.length - 1].content = m.app_research_chat_error()
        return updated
      })
      setStreaming(false)
      return
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const payload = JSON.parse(line.slice(6))
          if (payload.type === 'token') {
            setMessages((prev) => {
              const updated = [...prev]
              updated[updated.length - 1].content += payload.content
              return updated
            })
          } else if (payload.type === 'done') {
            setMessages((prev) => {
              const updated = [...prev]
              updated[updated.length - 1].citations = payload.citations
              return updated
            })
          }
        } catch {}
      }
    }

    setStreaming(false)
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left panel: sources */}
      <div className="w-72 flex-shrink-0 border-r p-4 overflow-y-auto space-y-3">
        <button
          onClick={onBack}
          className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
        >
          &larr; Notebooks
        </button>
        <h2 className="font-serif text-base font-bold text-[var(--color-purple-deep)]">
          {notebook.name}
        </h2>
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium">{m.app_research_sources_heading()}</span>
          <div className="flex gap-1">
            <label className="cursor-pointer rounded p-1 hover:bg-[var(--color-muted)]" title={m.app_research_add_source()}>
              <Upload size={14} />
              <input type="file" className="sr-only" accept=".pdf,.docx,.xlsx,.pptx" onChange={handleFileUpload} />
            </label>
            <button
              className="rounded p-1 hover:bg-[var(--color-muted)]"
              onClick={() => setShowUrlInput((v) => !v)}
              title="URL toevoegen"
            >
              <Link size={14} />
            </button>
          </div>
        </div>

        {showUrlInput && (
          <form onSubmit={handleUrlSubmit} className="flex gap-1">
            <input
              className="flex-1 rounded-md border px-2 py-1 text-xs"
              placeholder="https://..."
              value={urlValue}
              onChange={(e) => setUrlValue(e.target.value)}
              autoFocus
            />
            <button type="submit" className="rounded bg-[var(--color-purple-accent)] px-2 py-1 text-xs text-white">
              +
            </button>
          </form>
        )}

        {sources.length === 0 ? (
          <p className="text-xs text-[var(--color-muted-foreground)]">{m.app_research_no_sources()}</p>
        ) : (
          <ul className="space-y-2">
            {sources.map((src) => (
              <li key={src.id} className="group flex items-start justify-between gap-1 text-xs">
                <div className="min-w-0">
                  <p className="truncate font-medium">{src.name}</p>
                  <p className={`text-xs ${statusColor(src.status)}`}>{statusLabel(src.status)}</p>
                  {src.status === 'error' && src.error_message && (
                    <p className="text-xs text-red-500 truncate" title={src.error_message}>
                      {src.error_message}
                    </p>
                  )}
                </div>
                <button
                  className="invisible group-hover:visible text-[var(--color-muted-foreground)] hover:text-red-500 flex-shrink-0 mt-0.5"
                  onClick={() => handleDeleteSource(src.id)}
                  title={m.app_research_source_delete()}
                >
                  <Trash2 size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Right panel: chat */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Mode toggle */}
        <div className="flex items-center gap-2 border-b px-4 py-2">
          <span className="text-xs font-medium">{m.app_research_chat_heading()}</span>
          <div className="ml-auto flex gap-1">
            {(['narrow', 'broad', 'web'] as const).map((m_val) => {
              const labels: Record<string, string> = {
                narrow: m.app_research_chat_mode_narrow(),
                broad: m.app_research_chat_mode_broad(),
                web: m.app_research_chat_mode_web(),
              }
              return (
                <button
                  key={m_val}
                  onClick={() => setMode(m_val)}
                  className={`rounded-full px-3 py-0.5 text-xs font-medium transition-colors ${
                    mode === m_val
                      ? 'bg-[var(--color-purple-accent)] text-white'
                      : 'text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]'
                  }`}
                >
                  {labels[m_val]}
                </button>
              )
            })}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.map((msg, i) => (
            <div key={i} className={msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
              <div className={`max-w-[80%] ${msg.role === 'user' ? 'bg-[var(--color-purple-accent)] text-white rounded-xl rounded-tr-sm px-4 py-2 text-sm' : 'text-sm'}`}>
                <p className="whitespace-pre-wrap">{msg.content}</p>
                {msg.role === 'assistant' && msg.citations && msg.citations.length > 0 && (
                  <CitationsBlock citations={msg.citations} />
                )}
              </div>
            </div>
          ))}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <form onSubmit={handleSend} className="border-t p-3 flex gap-2 items-end">
          <textarea
            className="flex-1 resize-none rounded-md border px-3 py-2 text-sm max-h-32"
            rows={1}
            placeholder={hasReadySources ? m.app_research_chat_placeholder() : m.app_research_chat_disabled_hint()}
            disabled={!hasReadySources || streaming}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSend(e as unknown as React.FormEvent)
              }
            }}
          />
          <button
            type="submit"
            disabled={!hasReadySources || !question.trim() || streaming}
            className="rounded-md bg-[var(--color-purple-accent)] p-2 text-white disabled:opacity-40"
          >
            <Send size={16} />
          </button>
        </form>
      </div>
    </div>
  )
}

function CitationsBlock({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-3 border-t pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
      >
        <span>{m.app_research_chat_sources_label()}</span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && (
        <ul className="mt-2 space-y-2">
          {citations.map((c, i) => (
            <li key={i} className="text-xs text-[var(--color-muted-foreground)]">
              <span className="font-medium">{c.source_name}</span>
              {c.page && <span> p.{c.page}</span>}
              <p className="italic mt-0.5 line-clamp-2">{c.excerpt}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function ResearchPage() {
  const auth = useAuth()
  const [notebooks, setNotebooks] = useState<Notebook[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [selected, setSelected] = useState<Notebook | null>(null)

  const token = auth.user?.access_token ?? ''

  useEffect(() => {
    if (!token) return
    fetch(`${API_BASE}/research/v1/notebooks`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.ok ? r.json() : { items: [] })
      .then((data) => setNotebooks(data.items))
      .finally(() => setLoading(false))
  }, [token])

  if (selected) {
    return (
      <div className="h-full">
        <NotebookDetail notebook={selected} token={token} onBack={() => setSelected(null)} />
      </div>
    )
  }

  return (
    <div className="p-8 space-y-6 max-w-4xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.app_tool_research_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.app_research_subtitle()}</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1 rounded-md bg-[var(--color-purple-accent)] px-3 py-2 text-sm text-white"
        >
          <Plus size={15} />
          {m.app_research_new_notebook()}
        </button>
      </div>

      {loading ? (
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.app_research_loading()}</p>
      ) : notebooks.length === 0 ? (
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.app_research_empty()}</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {notebooks.map((nb) => (
            <button
              key={nb.id}
              onClick={() => setSelected(nb)}
              className="group flex flex-col gap-2 rounded-xl border bg-[var(--color-card)] p-5 text-left transition-shadow hover:shadow-md"
            >
              <BookOpen size={18} strokeWidth={1.5} className="text-[var(--color-purple-accent)]" />
              <div>
                <p className="text-sm font-medium text-[var(--color-purple-deep)] group-hover:text-[var(--color-purple-accent)] transition-colors">
                  {nb.name}
                </p>
                {nb.description && (
                  <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)] line-clamp-2">
                    {nb.description}
                  </p>
                )}
                <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                  {nb.sources_count} {nb.sources_count === 1 ? 'bron' : 'bronnen'} &middot; {nb.default_mode}
                </p>
              </div>
            </button>
          ))}
        </div>
      )}

      {showCreate && (
        <CreateNotebookModal
          token={token}
          onCreated={(nb) => {
            setNotebooks((prev) => [nb, ...prev])
            setShowCreate(false)
            setSelected(nb)
          }}
          onClose={() => setShowCreate(false)}
        />
      )}
    </div>
  )
}
