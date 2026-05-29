import { useEffect, useRef, useState } from 'react'
import { ArrowUp, ChevronDown, MessageSquare, Pencil, Share2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import * as m from '@/paraglide/messages'

export interface WidgetChatSurfaceProps {
  botName: string
  chatEndpoint: string
  sessionToken: string
  description?: string
  welcomeMessage?: string
  conversationStarters?: string[]
  hideDisclaimer?: boolean
  primaryColor?: string
  theme?: 'light' | 'dark'
  showSources?: boolean
  showMeta?: boolean
  collectUserInfo?: boolean
  pageContextEnabled?: boolean
  variant?: 'public' | 'admin-preview'
  onClose?: () => void
  shareUrl?: string
}

interface MessageSource {
  label: string
  title: string
  url: string
}

interface AgentActivity {
  step: string
  label: string
  detail?: string
  count?: number
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: MessageSource[]
  activity?: AgentActivity[]
}

interface PageContext {
  url: string
  path: string
  title?: string
  excerpt?: string
}

const MAX_PAGE_CONTEXT_VALUE_CHARS = 2048
const MAX_PAGE_EXCERPT_CHARS = 2000

function cleanContextValue(value: string | undefined, maxChars = MAX_PAGE_CONTEXT_VALUE_CHARS) {
  const cleaned = value?.replace(/\s+/g, ' ').trim()
  return cleaned ? cleaned.slice(0, maxChars) : undefined
}

function collectPageContext(): PageContext | undefined {
  try {
    const url = new URL(window.location.href)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return undefined
    url.search = ''
    url.hash = ''
    const root =
      document.querySelector<HTMLElement>('main, article, [role="main"]') ?? document.body
    const clone = root?.cloneNode(true) as HTMLElement | undefined
    clone
      ?.querySelectorAll('script, style, noscript, svg, nav, header, footer, form, input, textarea, select, button, iframe, [data-widget-chat-surface]')
      .forEach((node) => node.remove())
    return {
      url: url.toString().slice(0, MAX_PAGE_CONTEXT_VALUE_CHARS),
      path: window.location.pathname.slice(0, 512),
      title: cleanContextValue(document.title),
      excerpt: cleanContextValue(clone?.innerText, MAX_PAGE_EXCERPT_CHARS),
    }
  } catch {
    return undefined
  }
}

function normalizeSources(rawSources: unknown): MessageSource[] {
  if (!Array.isArray(rawSources)) return []
  const seen = new Set<string>()
  const normalized: MessageSource[] = []
  for (const raw of rawSources) {
    if (!raw || typeof raw !== 'object') continue
    const source = raw as Partial<MessageSource>
    const label = typeof source.label === 'string' ? source.label.trim() : ''
    const title = typeof source.title === 'string' && source.title.trim() ? source.title.trim() : `Source ${label}`
    const rawUrl = typeof source.url === 'string' ? source.url.trim() : ''
    const url = /^https?:\/\//i.test(rawUrl) ? rawUrl : ''
    if (!/^\d+$/.test(label) || seen.has(label)) continue
    normalized.push({ label, title, url })
    seen.add(label)
  }
  return normalized
}

function normalizeActivity(rawActivity: unknown): AgentActivity[] {
  if (!Array.isArray(rawActivity)) return []
  const seen = new Set<string>()
  const normalized: AgentActivity[] = []
  for (const raw of rawActivity) {
    if (!raw || typeof raw !== 'object') continue
    const item = raw as Partial<AgentActivity>
    const step = typeof item.step === 'string' ? item.step.trim() : ''
    const label = typeof item.label === 'string' ? item.label.trim() : ''
    if (!step || !label || seen.has(step)) continue
    const detail = typeof item.detail === 'string' && item.detail.trim() ? item.detail.trim() : undefined
    const count = typeof item.count === 'number' && Number.isFinite(item.count) ? item.count : undefined
    normalized.push({ step, label, detail, count })
    seen.add(step)
  }
  return normalized
}

export function WidgetChatSurface({
  botName,
  chatEndpoint,
  sessionToken,
  description = '',
  welcomeMessage = '',
  conversationStarters = [],
  hideDisclaimer = false,
  primaryColor = '#fcaa2d',
  theme = 'light',
  showSources = true,
  showMeta = false,
  collectUserInfo = false,
  pageContextEnabled = false,
  variant = 'public',
  onClose,
  shareUrl,
}: WidgetChatSurfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [copied, setCopied] = useState(false)
  const [visitorName, setVisitorName] = useState('')
  const [visitorEmail, setVisitorEmail] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const starters = conversationStarters.filter(Boolean).slice(0, 6)
  const primaryFaint = `${primaryColor}14`
  const isDark = theme === 'dark'
  const visitorInfoComplete =
    !collectUserInfo ||
    (visitorName.trim().length > 1 && visitorEmail.trim().includes('@'))

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isStreaming])

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
    if (!content || isStreaming || !visitorInfoComplete) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'

    const userMsg: ChatMessage = { role: 'user', content }
    const placeholder: ChatMessage = { role: 'assistant', content: '' }
    setMessages((prev) => [...prev, userMsg, placeholder])
    setIsStreaming(true)

    try {
      const res = await fetch(chatEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionToken}`,
        },
        body: JSON.stringify({
          messages: withVisitorInfo([...messages, userMsg]).map(({ role, content }) => ({ role, content })),
          stream: true,
          page_context: pageContextEnabled ? collectPageContext() : undefined,
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
            if (token) appendAssistantToken(token)
            const sources = normalizeSources(parsed.choices?.[0]?.delta?.sources)
            if (sources.length > 0) setLastAssistantSources(sources)
            const activity = normalizeActivity(parsed.choices?.[0]?.delta?.activity)
            if (activity.length > 0) appendLastAssistantActivity(activity)
          } catch {
            // Ignore malformed streaming chunks.
          }
        }
      }
    } catch (err) {
      appendAssistantToken(
        `⚠️ ${err instanceof Error ? err.message : 'Er ging iets mis'}`,
        true,
      )
    } finally {
      setIsStreaming(false)
    }
  }

  function appendAssistantToken(token: string, replace = false) {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last && last.role === 'assistant') {
        next[next.length - 1] = {
          ...last,
          content: replace ? token : last.content + token,
        }
      }
      return next
    })
  }

  function setLastAssistantSources(sources: MessageSource[]) {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last && last.role === 'assistant') {
        next[next.length - 1] = { ...last, sources }
      }
      return next
    })
  }

  function appendLastAssistantActivity(activity: AgentActivity[]) {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last && last.role === 'assistant') {
        const existing = last.activity ?? []
        const seen = new Set(existing.map((item) => item.step))
        const appended = activity.filter((item) => !seen.has(item.step))
        next[next.length - 1] = { ...last, activity: [...existing, ...appended] }
      }
      return next
    })
  }

  function withVisitorInfo(nextMessages: ChatMessage[]): ChatMessage[] {
    if (!collectUserInfo) return nextMessages
    const name = visitorName.trim()
    const email = visitorEmail.trim()
    if (!name || !email) return nextMessages
    let added = false
    return nextMessages.map((message) => {
      if (added || message.role !== 'user') return message
      added = true
      return {
        ...message,
        content: `Visitor details:\nName: ${name}\nEmail: ${email}\n\nMessage:\n${message.content}`,
      }
    })
  }

  function newConversation() {
    setMessages([])
    setInput('')
  }

  function copyShareLink() {
    if (!shareUrl) return
    void navigator.clipboard.writeText(shareUrl).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div
      data-widget-chat-surface
      className={`fixed inset-0 z-[60] flex flex-col ${isDark ? 'bg-[#191918] text-[#fffef2]' : 'bg-white text-gray-900'}`}
      style={{ height: '100vh' }}
    >
      <div className={`flex h-14 shrink-0 items-center justify-between border-b px-4 sm:px-6 ${isDark ? 'border-white/10 bg-[#191918]' : 'border-gray-200 bg-white'}`}>
        <div className="flex min-w-0 items-center gap-3">
          <div
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
            style={{ backgroundColor: primaryColor }}
          >
            <MessageSquare className="h-4 w-4 text-white" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <h2 className={`truncate text-sm font-display-medium leading-none ${isDark ? 'text-[#fffef2]' : 'text-gray-900'}`}>{botName}</h2>
            <p className={`mt-0.5 flex items-center gap-1 text-[11px] leading-none ${isDark ? 'text-[#fffef2]/50' : 'text-gray-400'}`}>
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
              {m.widget_chat_status_online()}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={newConversation}
            className="h-8 rounded-lg pl-2.5 pr-3 text-[13px]"
          >
            <Pencil className="h-3.5 w-3.5" strokeWidth={2} />
            <span className="hidden sm:inline">{m.widget_chat_new_conversation()}</span>
          </Button>
          {shareUrl && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={copyShareLink}
              className="h-8 rounded-lg pl-2.5 pr-3 text-[13px]"
            >
              <Share2 className="h-3.5 w-3.5" strokeWidth={2} />
              <span className="hidden sm:inline">
                {copied ? m.widget_chat_copied() : m.widget_chat_share_link()}
              </span>
            </Button>
          )}
          {onClose && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={onClose}
              aria-label={m.widget_chat_close()}
              className={`ml-1 h-8 w-8 ${isDark ? 'text-[#fffef2]/55' : 'text-gray-400'}`}
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      <div className={`flex-1 overflow-y-auto ${isDark ? 'bg-[#191918]' : 'bg-white'}`}>
        <div className={`mx-auto max-w-3xl px-4 sm:px-6 ${messages.length === 0 ? 'h-full flex flex-col' : 'py-6'}`}>
          {messages.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 pb-8 text-center">
              <div
                className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl"
                style={{ backgroundColor: primaryFaint }}
              >
                <MessageSquare className="h-8 w-8" style={{ color: primaryColor }} strokeWidth={1.5} />
              </div>
              <h3 className={`text-lg font-semibold ${isDark ? 'text-[#fffef2]' : 'text-gray-900'}`}>{botName}</h3>
              <p className={`mt-1 max-w-md text-sm ${isDark ? 'text-[#fffef2]/65' : 'text-gray-500'}`}>
                {welcomeMessage || description || m.widget_chat_default_empty_state()}
              </p>
              {starters.length > 0 && (
                <div className="mt-8 flex max-w-lg flex-wrap justify-center gap-2">
                  {starters.map((starter) => (
                    <Button
                      key={starter}
                      type="button"
                      variant="secondary"
                      onClick={() => void sendMessage(starter)}
                      className={`h-auto rounded-xl px-4 py-2.5 text-[13px] ${isDark ? 'bg-white/10 text-[#fffef2] hover:bg-white/15' : 'bg-[var(--color-rl-cream)] text-gray-700 hover:bg-[var(--color-rl-cream)]/70'}`}
                    >
                      {starter}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((msg, index) => (
                <MessageBubble
                  key={index}
                  message={msg}
                  isLast={index === messages.length - 1}
                  isStreaming={isStreaming}
                  primaryColor={primaryColor}
                  primaryFaint={primaryFaint}
                  variant={variant}
                  isDark={isDark}
                  showSources={showSources}
                  showMeta={showMeta}
                />
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      <div className={`shrink-0 ${isDark ? 'bg-[#191918]' : 'bg-white'}`}>
        <div className="mx-auto max-w-3xl px-4 pb-4 pt-2 sm:px-6">
          {collectUserInfo && (
            <div className="mb-3">
              <p className={`mb-2 text-xs ${isDark ? 'text-[#fffef2]/55' : 'text-gray-400'}`}>
                {m.widget_chat_user_info_help()}
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                <input
                  type="text"
                  autoComplete="name"
                  value={visitorName}
                  onChange={(e) => setVisitorName(e.target.value)}
                  placeholder={m.widget_chat_user_info_name()}
                  className={`min-w-0 rounded-xl border px-3 py-2 text-sm outline-none transition-colors focus:border-gray-300 ${isDark ? 'border-white/10 bg-white/5 text-[#fffef2] placeholder:text-[#fffef2]/35' : 'border-gray-200 bg-white text-gray-900 placeholder:text-gray-400'}`}
                />
                <input
                  type="email"
                  autoComplete="email"
                  value={visitorEmail}
                  onChange={(e) => setVisitorEmail(e.target.value)}
                  placeholder={m.widget_chat_user_info_email()}
                  className={`min-w-0 rounded-xl border px-3 py-2 text-sm outline-none transition-colors focus:border-gray-300 ${isDark ? 'border-white/10 bg-white/5 text-[#fffef2] placeholder:text-[#fffef2]/35' : 'border-gray-200 bg-white text-gray-900 placeholder:text-gray-400'}`}
                />
              </div>
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void sendMessage()
            }}
            className={`flex items-end gap-2 rounded-3xl border py-2 pl-5 pr-2 transition-colors focus-within:border-gray-300 ${isDark ? 'border-white/10 bg-white/5' : 'border-gray-200 bg-white'}`}
          >
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value)
                autoResize()
              }}
              onKeyDown={handleKeyDown}
              placeholder={m.widget_chat_input_placeholder()}
              rows={1}
              disabled={isStreaming}
              className={`max-h-40 min-h-[28px] flex-1 resize-none border-0 bg-transparent px-0 py-1.5 text-[15px] leading-6 focus:ring-0 ${isDark ? 'text-[#fffef2] placeholder:text-[#fffef2]/35' : 'text-gray-900'}`}
            />
            <Button
              type="submit"
              disabled={!input.trim() || isStreaming || !visitorInfoComplete}
              aria-label={m.widget_chat_send()}
              size="icon"
              className={
                input.trim() && !isStreaming && visitorInfoComplete
                  ? 'h-10 w-10 shrink-0 self-end rounded-full text-white hover:scale-[1.04] active:scale-95'
                  : 'h-10 w-10 shrink-0 cursor-not-allowed self-end rounded-full bg-gray-100 text-gray-400'
              }
              style={input.trim() && !isStreaming && visitorInfoComplete ? { backgroundColor: primaryColor } : {}}
            >
              <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
            </Button>
          </form>
          {!hideDisclaimer && (
            <p className={`mt-2.5 text-center text-[11px] ${isDark ? 'text-[#fffef2]/45' : 'text-gray-400'}`}>
              {m.widget_ai_disclaimer()}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function MessageBubble({
  message,
  isLast,
  isStreaming,
  primaryColor,
  primaryFaint,
  variant,
  isDark,
  showSources,
  showMeta,
}: {
  message: ChatMessage
  isLast: boolean
  isStreaming: boolean
  primaryColor: string
  primaryFaint: string
  variant: 'public' | 'admin-preview'
  isDark: boolean
  showSources: boolean
  showMeta: boolean
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div
          className={variant === 'admin-preview'
            ? 'max-w-[75%] rounded-2xl rounded-br-md px-5 py-3 text-white'
            : 'max-w-[75%] rounded-2xl rounded-br-md px-4 py-2.5 text-white'}
          style={{ backgroundColor: primaryColor }}
        >
          <p className="whitespace-pre-line break-words text-[14px] leading-relaxed">
            {message.content}
          </p>
        </div>
      </div>
    )
  }

  if (variant === 'admin-preview') {
    return (
      <div className="flex gap-4">
        <div
          className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl"
          style={{ backgroundColor: primaryFaint }}
        >
          <MessageSquare className="h-4 w-4" style={{ color: primaryColor }} strokeWidth={2} />
        </div>
        <div className="min-w-0 flex-1">
          <div className={`whitespace-pre-line break-words text-[14px] leading-[1.75] ${isDark ? 'text-[#fffef2]' : 'text-gray-900'}`}>
            {message.content || (isStreaming && isLast ? '…' : '')}
          </div>
          <SourceDetails message={message} showSources={showSources} showMeta={showMeta} isDark={isDark} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start">
      {message.content ? (
        <div>
          <div className={`max-w-[75%] rounded-2xl rounded-bl-md px-4 py-2.5 ${isDark ? 'bg-white/10' : 'bg-[var(--color-rl-cream)]'}`}>
            <div className={`whitespace-pre-line break-words text-[14px] leading-[1.6] ${isDark ? 'text-[#fffef2]' : 'text-gray-900'}`}>
              {message.content}
            </div>
          </div>
          <SourceDetails message={message} showSources={showSources} showMeta={showMeta} isDark={isDark} />
        </div>
      ) : isStreaming && isLast ? (
        <div className={`inline-flex items-center gap-1 rounded-2xl rounded-bl-md px-4 py-3 ${isDark ? 'bg-white/10' : 'bg-[var(--color-rl-cream)]'}`}>
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:0ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:150ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:300ms]" />
        </div>
      ) : null}
    </div>
  )
}

function SourceDetails({
  message,
  showSources,
  showMeta,
  isDark,
}: {
  message: ChatMessage
  showSources: boolean
  showMeta: boolean
  isDark: boolean
}) {
  const sources = message.sources ?? []
  const activity = message.activity ?? []
  const sourceCountLabel = sources.length === 1 ? '1 bron' : `${sources.length} bronnen`
  const activityCountLabel = activity.length === 1 ? '1 stap' : `${activity.length} stappen`
  if (
    message.role !== 'assistant' ||
    (!showSources && !showMeta) ||
    (sources.length === 0 && activity.length === 0)
  ) return null
  return (
    <div className="mt-2 max-w-xl space-y-1.5">
      {showSources && sources.length > 0 && (
        <details className={`group rounded-xl border ${isDark ? 'border-white/10 bg-white/[0.04]' : 'border-gray-200 bg-gray-50/70'}`}>
          <summary className={`flex min-h-9 cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs ${isDark ? 'text-[#fffef2]/60 hover:text-[#fffef2]' : 'text-gray-500 hover:text-gray-900'} [&::-webkit-details-marker]:hidden`}>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 -rotate-90 transition-transform group-open:rotate-0" strokeWidth={2} />
            <span className="min-w-0 flex-1 font-medium">{m.widget_chat_sources_label()}</span>
            <span className={isDark ? 'text-[#fffef2]/40' : 'text-gray-400'}>{sourceCountLabel}</span>
          </summary>
          <ol className="space-y-1 px-3 pb-3">
            {sources.map((source) => (
              <li key={`${source.label}-${source.url}`}>
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`inline-flex max-w-full items-center gap-2 rounded-full border px-2.5 py-1.5 text-xs no-underline transition-colors ${isDark ? 'border-white/10 bg-white/5 text-[#fffef2] hover:bg-white/10' : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'}`}
                  >
                    <span
                      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white"
                      style={{ backgroundColor: 'var(--color-rl-dark)' }}
                    >
                      {source.label}
                    </span>
                    <span className="truncate">{source.title}</span>
                  </a>
                ) : (
                  <span className={`inline-flex max-w-full items-center gap-2 rounded-full border px-2.5 py-1.5 text-xs ${isDark ? 'border-white/10 bg-white/5 text-[#fffef2]/70' : 'border-gray-200 bg-white text-gray-600'}`}>
                    <span
                      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white"
                      style={{ backgroundColor: 'var(--color-rl-dark)' }}
                    >
                      {source.label}
                    </span>
                    <span className="truncate">{source.title}</span>
                  </span>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
      {showMeta && (
        <details className={`group rounded-xl border ${isDark ? 'border-white/10 bg-white/[0.03]' : 'border-gray-200 bg-gray-50/50'}`}>
          <summary className={`flex min-h-9 cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs ${isDark ? 'text-[#fffef2]/55 hover:text-[#fffef2]' : 'text-gray-500 hover:text-gray-900'} [&::-webkit-details-marker]:hidden`}>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 -rotate-90 transition-transform group-open:rotate-0" strokeWidth={2} />
            <span className="min-w-0 flex-1 font-medium">Agent activiteit</span>
            <span className={isDark ? 'text-[#fffef2]/40' : 'text-gray-400'}>
              {activity.length > 0 ? activityCountLabel : sourceCountLabel}
            </span>
          </summary>
          <div className={`space-y-2 px-3 pb-3 text-[11px] leading-relaxed ${isDark ? 'text-[#fffef2]/55' : 'text-gray-500'}`}>
            {sources.length > 0 && (
              <p>
                {sources.length === 1
                  ? m.widget_chat_meta_sources_one()
                  : m.widget_chat_meta_sources_many({ count: String(sources.length) })}
              </p>
            )}
            {activity.length > 0 && (
              <ol className="space-y-1.5">
                {activity.map((item) => (
                  <li key={item.step} className="flex gap-2">
                    <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${isDark ? 'bg-[#fffef2]/35' : 'bg-gray-300'}`} />
                    <span className="min-w-0">
                      <span className={isDark ? 'font-medium text-[#fffef2]/75' : 'font-medium text-gray-700'}>{item.label}</span>
                      {item.detail && <span> {item.detail}</span>}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </details>
      )}
    </div>
  )
}
