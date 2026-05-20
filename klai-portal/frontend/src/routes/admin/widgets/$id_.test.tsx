import { useEffect, useRef, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ArrowUp, MessageSquare, Pencil, X } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import type { WidgetDetailResponse } from './-types'

// 1:1 port of TWD's PublicBot.vue layout to a Klai-admin React route.
// Fetches the widget's PUBLIC config (origin-checked — the Test button
// auto-adds window.location.origin to allowed_origins), uses the
// returned session_token to talk to /partner/v1/chat/completions.
// Renders the TWD fullscreen layout: top bar, hero with suggestion
// chips on empty state, message stack, rounded-input with arrow send,
// disclaimer footer.

export const Route = createFileRoute('/admin/widgets/$id_/test')({
  component: WidgetTestPage,
})

interface PreviewSession {
  session_token: string
  chat_endpoint: string
  session_expires_at: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

function WidgetTestPage() {
  const { id } = Route.useParams()

  const widgetQuery = useQuery<WidgetDetailResponse>({
    queryKey: ['admin-widget-detail', id],
    queryFn: () => apiFetch<WidgetDetailResponse>(`/api/admin/widgets/${id}`),
  })

  // Use the admin preview-session endpoint — admin cookie auth, no
  // Origin gate. The widget detail (admin auth) carries all the layout
  // config; we only need a chat session_token from here.
  const sessionQuery = useQuery<PreviewSession>({
    queryKey: ['widget-preview-session', id],
    queryFn: () => apiFetch<PreviewSession>(`/api/admin/widgets/${id}/preview-session`),
    retry: false,
  })

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isStreaming])

  // Hide the help-widget bubble that the portal SPA also renders.
  useEffect(() => {
    const style = document.createElement('style')
    style.textContent = `[data-help-id="chat-help-bubble"], .klai-help-button { display: none !important; }`
    document.head.appendChild(style)
    return () => { style.remove() }
  }, [])

  if (widgetQuery.isPending || sessionQuery.isPending) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-900" />
      </div>
    )
  }

  if (widgetQuery.error || !widgetQuery.data) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white px-6">
        <p className="text-sm text-[var(--color-destructive)]">
          {widgetQuery.error instanceof Error ? widgetQuery.error.message : 'Widget kon niet geladen worden.'}
        </p>
      </div>
    )
  }
  if (sessionQuery.error || !sessionQuery.data) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white px-6">
        <div className="max-w-md text-center">
          <p className="text-sm font-medium text-gray-900">Kon preview-sessie niet starten</p>
          <p className="mt-1 text-xs text-gray-400">
            {sessionQuery.error instanceof Error ? sessionQuery.error.message : 'Onbekende fout'}
          </p>
        </div>
      </div>
    )
  }

  const widget = widgetQuery.data
  const session = sessionQuery.data
  const primary = widget.widget_config.primary_color || '#fcaa2d'
  const botName = widget.widget_config.title || widget.name
  const description = widget.description || ''
  const starters = (widget.widget_config.conversation_starters ?? []).filter(Boolean).slice(0, 6)
  const hideDisclaimer = widget.widget_config.hide_disclaimer ?? false
  const welcomeMessage = widget.widget_config.welcome_message

  function autoResize() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void sendMessage()
    }
  }

  async function sendMessage(override?: string) {
    const content = (override ?? input).trim()
    if (!content || isStreaming) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'

    const userMsg: ChatMessage = { role: 'user', content }
    const placeholder: ChatMessage = { role: 'assistant', content: '' }
    setMessages((prev) => [...prev, userMsg, placeholder])
    setIsStreaming(true)

    try {
      const res = await fetch(session.chat_endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.session_token}`,
        },
        body: JSON.stringify({
          messages: [...messages, userMsg].map(({ role, content }) => ({ role, content })),
          stream: true,
        }),
      })
      if (!res.ok || !res.body) throw new Error(`chat ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const data = line.slice(5).trim()
          if (!data || data === '[DONE]') continue
          try {
            const parsed = JSON.parse(data)
            const token =
              parsed.choices?.[0]?.delta?.content ??
              parsed.choices?.[0]?.message?.content ??
              parsed.content ??
              ''
            if (token) {
              setMessages((prev) => {
                const next = [...prev]
                const last = next[next.length - 1]
                if (last && last.role === 'assistant') {
                  next[next.length - 1] = { ...last, content: last.content + token }
                }
                return next
              })
            }
          } catch {
            // ignore malformed chunk
          }
        }
      }
    } catch (err) {
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last && last.role === 'assistant') {
          next[next.length - 1] = { ...last, content: `⚠️ ${err instanceof Error ? err.message : 'Er ging iets mis'}` }
        }
        return next
      })
    } finally {
      setIsStreaming(false)
    }
  }

  function newConversation() {
    setMessages([])
    setInput('')
  }

  const primaryFaint = `${primary}14` // 8% alpha hex

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-white" style={{ height: '100vh' }}>
      {/* Header */}
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg" style={{ backgroundColor: primary }}>
            <MessageSquare className="h-4 w-4 text-white" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-display-medium leading-none text-gray-900">{botName}</h2>
            <p className="mt-0.5 flex items-center gap-1 text-[11px] leading-none text-gray-400">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
              Online
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={newConversation}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-gray-200 bg-white pl-2.5 pr-3 text-[13px] font-medium text-gray-700 klai-hover"
          >
            <Pencil className="h-3.5 w-3.5" strokeWidth={2} />
            <span className="hidden sm:inline">Nieuw gesprek</span>
          </button>
          <button
            type="button"
            onClick={() => window.close()}
            aria-label="Sluiten"
            className="ml-1 inline-flex h-8 w-8 items-center justify-center rounded-full text-gray-400 klai-hover"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Chat area */}
      <div className="flex-1 overflow-y-auto bg-white">
        <div className={`mx-auto max-w-3xl px-4 sm:px-6 ${messages.length === 0 ? 'h-full flex flex-col' : 'py-6'}`}>
          {messages.length === 0 ? (
            // Empty state hero
            <div className="flex flex-1 flex-col items-center justify-center px-4 pb-8 text-center">
              <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl shadow-sm" style={{ backgroundColor: primaryFaint }}>
                <MessageSquare className="h-8 w-8" style={{ color: primary }} strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-semibold text-gray-900">{botName}</h3>
              <p className="mt-1 max-w-md text-sm text-gray-500">
                {description || welcomeMessage || 'Stel je vraag en ik help je verder.'}
              </p>
              {starters.length > 0 && (
                <div className="mt-8 flex max-w-lg flex-wrap justify-center gap-2">
                  {starters.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => void sendMessage(s)}
                      className="rounded-xl border border-gray-200 bg-[var(--color-rl-cream)] px-4 py-2.5 text-[13px] text-gray-700 transition-colors hover:border-gray-300 hover:bg-[var(--color-rl-cream)]/70"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            // Messages
            <div className="space-y-6">
              {messages.map((msg, i) => (
                msg.role === 'user' ? (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[75%] rounded-2xl rounded-br-md px-5 py-3 text-white shadow-sm" style={{ backgroundColor: primary }}>
                      <p className="whitespace-pre-line break-words text-[14px] leading-relaxed">{msg.content}</p>
                    </div>
                  </div>
                ) : (
                  <div key={i} className="flex gap-4">
                    <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl shadow-sm" style={{ backgroundColor: primaryFaint }}>
                      <MessageSquare className="h-4 w-4" style={{ color: primary }} strokeWidth={2} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="whitespace-pre-line break-words text-[14px] leading-[1.75] text-gray-900">
                        {msg.content || (isStreaming && i === messages.length - 1 ? '…' : '')}
                      </div>
                    </div>
                  </div>
                )
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* Input area */}
      <div className="shrink-0 bg-white">
        <div className="mx-auto max-w-3xl px-4 pb-4 pt-2 sm:px-6">
          <form
            onSubmit={(e) => { e.preventDefault(); void sendMessage() }}
            className="flex items-end gap-2 rounded-3xl border border-gray-200 bg-white py-2 pl-5 pr-2 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_8px_24px_-8px_rgba(16,24,40,0.08)] transition-all focus-within:border-transparent"
            style={{ '--ring-color': `${primary}29` } as React.CSSProperties}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => { setInput(e.target.value); autoResize() }}
              onKeyDown={handleKeyDown}
              placeholder="Stel je vraag..."
              rows={1}
              disabled={isStreaming}
              className="max-h-40 min-h-[28px] flex-1 resize-none bg-transparent py-1.5 text-[15px] leading-6 text-gray-900 outline-none placeholder:text-gray-400"
            />
            <button
              type="submit"
              disabled={!input.trim() || isStreaming}
              aria-label="Verstuur"
              className={`flex h-10 w-10 shrink-0 items-center justify-center self-end rounded-full transition-all ${
                input.trim() && !isStreaming
                  ? 'text-white hover:scale-[1.04] active:scale-95'
                  : 'cursor-not-allowed bg-gray-100 text-gray-400'
              }`}
              style={input.trim() && !isStreaming ? { backgroundColor: primary } : {}}
            >
              <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
            </button>
          </form>
          {!hideDisclaimer && (
            <p className="mt-2.5 text-center text-[11px] text-gray-400">
              AI-antwoorden kunnen fouten bevatten. Verifieer belangrijke informatie altijd bij de bron.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
