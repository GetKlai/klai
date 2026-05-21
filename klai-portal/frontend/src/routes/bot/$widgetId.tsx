import { useEffect, useRef, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ArrowUp, MessageSquare, Pencil, Share2 } from 'lucide-react'

// Public bot share-page — TWD's PublicBot.vue equivalent at
// /bot/<widget_id>. NO admin auth required: anyone with the link can
// open and chat with the bot. Security boundary is the HS256 JWT
// session_token returned by /partner/v1/public-bot-config (no Origin
// check there — widget_id is the public access key).

export const Route = createFileRoute('/bot/$widgetId')({
  component: PublicBotPage,
})

interface PublicConfig {
  title: string
  welcome_message: string
  chat_endpoint: string
  session_token: string
  session_expires_at: string
  css_variables?: Record<string, string>
  conversation_starters?: string[]
  hide_disclaimer?: boolean
  primary_color?: string
  name?: string
  description?: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

function PublicBotPage() {
  const { widgetId } = Route.useParams()

  const configQuery = useQuery<PublicConfig>({
    queryKey: ['public-bot-config', widgetId],
    queryFn: async () => {
      const res = await fetch(`/partner/v1/public-bot-config?id=${encodeURIComponent(widgetId)}`)
      if (!res.ok) throw new Error(`config ${res.status}`)
      return (await res.json()) as PublicConfig
    },
    retry: false,
  })

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [copied, setCopied] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isStreaming])

  // Hide the global Klai Help widget (klai-chat.js floating bubble loaded
  // site-wide in index.html) while this page is mounted — it would visually
  // collide with the embedded bot's own UI.
  useEffect(() => {
    const styleId = 'klai-public-bot-hide-help-widget'
    let style = document.getElementById(styleId) as HTMLStyleElement | null
    if (!style) {
      style = document.createElement('style')
      style.id = styleId
      style.textContent =
        '.klai-bubble, #klai-widget-root { display: none !important; }'
      document.head.appendChild(style)
    }
    return () => {
      style?.remove()
    }
  }, [])

  if (configQuery.isPending) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-gray-900" />
      </div>
    )
  }
  if (configQuery.error || !configQuery.data) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white px-6">
        <div className="max-w-md text-center">
          <p className="text-sm font-medium text-gray-900">Deze bot is niet beschikbaar</p>
          <p className="mt-1 text-xs text-gray-400">
            {configQuery.error instanceof Error ? configQuery.error.message : 'Onbekende fout'}
          </p>
        </div>
      </div>
    )
  }

  const cfg = configQuery.data
  const primary = cfg.primary_color || '#fcaa2d'
  const botName = cfg.title || cfg.name || 'Klai bot'
  const description = cfg.description || ''
  const starters = (cfg.conversation_starters ?? []).filter(Boolean).slice(0, 6)
  const hideDisclaimer = cfg.hide_disclaimer ?? false
  const primaryFaint = `${primary}14`

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
      const res = await fetch(cfg.chat_endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${cfg.session_token}`,
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

  function copyShareLink() {
    void navigator.clipboard.writeText(window.location.href).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

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
            onClick={copyShareLink}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-gray-200 bg-white pl-2.5 pr-3 text-[13px] font-medium text-gray-700 klai-hover"
          >
            <Share2 className="h-3.5 w-3.5" strokeWidth={2} />
            <span className="hidden sm:inline">{copied ? 'Gekopieerd' : 'Deel link'}</span>
          </button>
        </div>
      </div>

      {/* Chat area */}
      <div className="flex-1 overflow-y-auto bg-white">
        <div className={`mx-auto max-w-3xl px-4 sm:px-6 ${messages.length === 0 ? 'h-full flex flex-col' : 'py-6'}`}>
          {messages.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 pb-8 text-center">
              <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl" style={{ backgroundColor: primaryFaint }}>
                <MessageSquare className="h-8 w-8" style={{ color: primary }} strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-semibold text-gray-900">{botName}</h3>
              <p className="mt-1 max-w-md text-sm text-gray-500">
                {description || cfg.welcome_message || 'Stel je vraag en ik help je verder.'}
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
            <div className="space-y-6">
              {messages.map((msg, i) =>
                msg.role === 'user' ? (
                  <div key={i} className="flex justify-end">
                    <div
                      className="max-w-[75%] rounded-2xl rounded-br-md px-4 py-2.5 text-white"
                      style={{ backgroundColor: primary }}
                    >
                      <p className="whitespace-pre-line break-words text-[14px] leading-relaxed">
                        {msg.content}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div key={i} className="flex justify-start">
                    {msg.content ? (
                      <div className="max-w-[75%] rounded-2xl rounded-bl-md bg-[var(--color-rl-cream)] px-4 py-2.5">
                        <div className="whitespace-pre-line break-words text-[14px] leading-[1.6] text-gray-900">
                          {msg.content}
                        </div>
                      </div>
                    ) : isStreaming && i === messages.length - 1 ? (
                      <div className="inline-flex items-center gap-1 rounded-2xl rounded-bl-md bg-[var(--color-rl-cream)] px-4 py-3">
                        <span
                          className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
                          style={{ animationDelay: '0ms' }}
                        />
                        <span
                          className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
                          style={{ animationDelay: '150ms' }}
                        />
                        <span
                          className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
                          style={{ animationDelay: '300ms' }}
                        />
                      </div>
                    ) : null}
                  </div>
                ),
              )}
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
            className="flex items-end gap-2 rounded-3xl border border-gray-200 bg-white py-2 pl-5 pr-2 transition-colors focus-within:border-gray-300"
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
