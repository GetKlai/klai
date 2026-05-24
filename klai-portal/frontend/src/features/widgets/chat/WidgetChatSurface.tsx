import { useEffect, useRef, useState } from 'react'
import { ArrowUp, MessageSquare, Pencil, Share2, X } from 'lucide-react'
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
  variant?: 'public' | 'admin-preview'
  onClose?: () => void
  shareUrl?: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
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
  variant = 'public',
  onClose,
  shareUrl,
}: WidgetChatSurfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [copied, setCopied] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const starters = conversationStarters.filter(Boolean).slice(0, 6)
  const primaryFaint = `${primaryColor}14`

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
    if (!content || isStreaming) return
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
          messages: [...messages, userMsg].map(({ role, content }) => ({
            role,
            content,
          })),
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
            if (token) appendAssistantToken(token)
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
    <div className="fixed inset-0 z-[60] flex flex-col bg-white" style={{ height: '100vh' }}>
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
            style={{ backgroundColor: primaryColor }}
          >
            <MessageSquare className="h-4 w-4 text-white" strokeWidth={1.75} />
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-display-medium leading-none text-gray-900">{botName}</h2>
            <p className="mt-0.5 flex items-center gap-1 text-[11px] leading-none text-gray-400">
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
              className="ml-1 h-8 w-8 text-gray-400"
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-white">
        <div className={`mx-auto max-w-3xl px-4 sm:px-6 ${messages.length === 0 ? 'h-full flex flex-col' : 'py-6'}`}>
          {messages.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 pb-8 text-center">
              <div
                className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl"
                style={{ backgroundColor: primaryFaint }}
              >
                <MessageSquare className="h-8 w-8" style={{ color: primaryColor }} strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-semibold text-gray-900">{botName}</h3>
              <p className="mt-1 max-w-md text-sm text-gray-500">
                {description || welcomeMessage || m.widget_chat_default_empty_state()}
              </p>
              {starters.length > 0 && (
                <div className="mt-8 flex max-w-lg flex-wrap justify-center gap-2">
                  {starters.map((starter) => (
                    <Button
                      key={starter}
                      type="button"
                      variant="secondary"
                      onClick={() => void sendMessage(starter)}
                      className="h-auto rounded-xl bg-[var(--color-rl-cream)] px-4 py-2.5 text-[13px] text-gray-700 hover:bg-[var(--color-rl-cream)]/70"
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
                />
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 bg-white">
        <div className="mx-auto max-w-3xl px-4 pb-4 pt-2 sm:px-6">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void sendMessage()
            }}
            className="flex items-end gap-2 rounded-3xl border border-gray-200 bg-white py-2 pl-5 pr-2 transition-colors focus-within:border-gray-300"
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
              className="max-h-40 min-h-[28px] flex-1 resize-none border-0 px-0 py-1.5 text-[15px] leading-6 focus:ring-0"
            />
            <Button
              type="submit"
              disabled={!input.trim() || isStreaming}
              aria-label={m.widget_chat_send()}
              size="icon"
              className={
                input.trim() && !isStreaming
                  ? 'h-10 w-10 shrink-0 self-end rounded-full text-white hover:scale-[1.04] active:scale-95'
                  : 'h-10 w-10 shrink-0 cursor-not-allowed self-end rounded-full bg-gray-100 text-gray-400'
              }
              style={input.trim() && !isStreaming ? { backgroundColor: primaryColor } : {}}
            >
              <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
            </Button>
          </form>
          {!hideDisclaimer && (
            <p className="mt-2.5 text-center text-[11px] text-gray-400">
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
}: {
  message: ChatMessage
  isLast: boolean
  isStreaming: boolean
  primaryColor: string
  primaryFaint: string
  variant: 'public' | 'admin-preview'
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
          <div className="whitespace-pre-line break-words text-[14px] leading-[1.75] text-gray-900">
            {message.content || (isStreaming && isLast ? '…' : '')}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start">
      {message.content ? (
        <div className="max-w-[75%] rounded-2xl rounded-bl-md bg-[var(--color-rl-cream)] px-4 py-2.5">
          <div className="whitespace-pre-line break-words text-[14px] leading-[1.6] text-gray-900">
            {message.content}
          </div>
        </div>
      ) : isStreaming && isLast ? (
        <div className="inline-flex items-center gap-1 rounded-2xl rounded-bl-md bg-[var(--color-rl-cream)] px-4 py-3">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:0ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:150ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:300ms]" />
        </div>
      ) : null}
    </div>
  )
}
