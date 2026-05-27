import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  ArrowLeft,
  Bug,
  CheckCircle2,
  Lightbulb,
  MessageSquare,
  Send,
  Sparkles,
  X,
  type LucideIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { apiFetch } from '@/lib/apiFetch'
import { cn } from '@/lib/utils'
import * as m from '@/paraglide/messages'

type AssistantMode = 'home' | 'question' | 'feedback' | 'problem'
type SubmissionState = 'idle' | 'submitting' | 'submitted' | 'error'
type FeedbackType = 'idea' | 'improvement' | 'confusing' | 'missing' | 'compliment' | 'other'
type ProblemSeverity = 'blocked' | 'workaround' | 'minor'

const KLAI_HELP_WIDGET_ID = 'wgt_5889c7533b1e558f7e92e251e8cd84b35eefb748'

interface AssistantContextPayload {
  page_url: string
  route_id?: string
  locale: string
  viewport: string
}

interface IntakePayload extends AssistantContextPayload {
  raw_text: string
}

interface ChatConfig {
  chat_endpoint: string
  session_token: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

async function fetchKlaiHelpChatConfig(): Promise<ChatConfig> {
  const res = await fetch(
    `/partner/v1/public-bot-config?id=${encodeURIComponent(KLAI_HELP_WIDGET_ID)}`,
  )
  if (!res.ok) throw new Error(`config ${res.status}`)
  return (await res.json()) as ChatConfig
}

function currentContext(): AssistantContextPayload {
  const locale =
    typeof document !== 'undefined' && document.documentElement.lang
      ? document.documentElement.lang
      : 'nl'
  return {
    page_url: typeof window === 'undefined' ? '' : window.location.href,
    route_id: typeof window === 'undefined' ? undefined : window.location.pathname,
    locale,
    viewport:
      typeof window === 'undefined'
        ? ''
        : `${window.innerWidth}x${window.innerHeight}`,
  }
}

export function KlaiAssistantLauncher() {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<AssistantMode>('home')

  function closePanel() {
    setOpen(false)
    setMode('home')
  }

  return (
    <>
      {open && (
        <KlaiAssistantPanel
          mode={mode}
          onModeChange={setMode}
          onClose={closePanel}
        />
      )}
      <div className="fixed bottom-5 right-5 z-[10002] sm:bottom-6 sm:right-6">
        <Button
          type="button"
          size="icon"
          aria-label={open ? m.klai_assistant_close() : m.klai_assistant_open()}
          aria-expanded={open}
          onClick={() => {
            if (open) {
              closePanel()
            } else {
              setOpen(true)
            }
          }}
          className="h-14 w-14 rounded-full border border-[var(--color-rl-border)] bg-[var(--color-rl-accent)] text-[var(--color-rl-dark)] shadow-lg hover:bg-[var(--color-rl-accent-hover)]"
        >
          {open ? <X className="h-5 w-5" /> : <Sparkles className="h-5 w-5" />}
        </Button>
      </div>
    </>
  )
}

function KlaiAssistantPanel({
  mode,
  onModeChange,
  onClose,
}: {
  mode: AssistantMode
  onModeChange: (mode: AssistantMode) => void
  onClose: () => void
}) {
  const showBack = mode !== 'home'

  return (
    <section
      aria-label={m.klai_assistant_title()}
      className="fixed bottom-24 right-4 z-[10002] flex max-h-[min(720px,calc(100vh-7rem))] w-[calc(100vw-2rem)] max-w-[420px] flex-col overflow-hidden rounded-xl border border-[var(--color-rl-border)] bg-[var(--color-rl-bg)] text-[var(--color-rl-dark)] shadow-2xl sm:right-6"
    >
      <div className="flex min-h-16 shrink-0 items-center justify-between bg-[var(--color-rl-accent)] px-4 py-3 text-[var(--color-rl-dark)]">
        <div className="flex min-w-0 items-center gap-2.5">
          {showBack && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => onModeChange('home')}
              aria-label={m.klai_assistant_back()}
              className="h-8 w-8 shrink-0 text-[var(--color-rl-dark)] hover:bg-[var(--color-rl-dark)]/10"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-[var(--color-rl-dark)]/10 text-[var(--color-rl-dark)]">
            <Sparkles className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-[var(--color-rl-dark)]">
              {m.klai_assistant_title()}
            </h2>
            <p className="truncate text-[11px] text-[var(--color-rl-dark)]/70">
              {m.klai_assistant_subtitle()}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label={m.klai_assistant_close()}
          className="h-8 w-8 shrink-0 text-[var(--color-rl-dark)] hover:bg-[var(--color-rl-dark)]/10"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {mode === 'home' && <AssistantHome onModeChange={onModeChange} />}
        {mode === 'question' && <QuestionView />}
        {mode === 'feedback' && <FeedbackView />}
        {mode === 'problem' && <ProblemView />}
      </div>
    </section>
  )
}

function AssistantHome({ onModeChange }: { onModeChange: (mode: AssistantMode) => void }) {
  const options: Array<{
    mode: AssistantMode
    icon: LucideIcon
    title: string
    description: string
  }> = [
    {
      mode: 'question',
      icon: MessageSquare,
      title: m.klai_assistant_option_question(),
      description: m.klai_assistant_option_question_desc(),
    },
    {
      mode: 'feedback',
      icon: Lightbulb,
      title: m.klai_assistant_option_feedback(),
      description: m.klai_assistant_option_feedback_desc(),
    },
    {
      mode: 'problem',
      icon: Bug,
      title: m.klai_assistant_option_problem(),
      description: m.klai_assistant_option_problem_desc(),
    },
  ]

  return (
    <div className="space-y-3">
      {options.map((option) => (
        <Button
          key={option.mode}
          type="button"
          variant="secondary"
          onClick={() => onModeChange(option.mode)}
          className="h-auto w-full justify-start rounded-xl border border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] px-3 py-3 text-left hover:border-[var(--color-rl-accent)] hover:bg-[var(--color-rl-accent)]/10"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--color-rl-accent)]/15 text-[var(--color-rl-dark)]">
            <option.icon className="h-4 w-4" />
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-[var(--color-rl-dark)]">
              {option.title}
            </span>
            <span className="mt-0.5 block whitespace-normal text-xs font-normal leading-5 text-[var(--color-rl-dark)]/60">
              {option.description}
            </span>
          </span>
        </Button>
      ))}
    </div>
  )
}

function QuestionView() {
  return (
    <ChatQuestionForm
      placeholder={m.klai_assistant_question_placeholder()}
      submitLabel={m.klai_assistant_question_submit()}
    />
  )
}

function FeedbackView() {
  const feedbackTypes: Array<{ value: FeedbackType; label: string }> = [
    { value: 'idea', label: m.klai_assistant_feedback_type_idea() },
    { value: 'improvement', label: m.klai_assistant_feedback_type_improvement() },
    { value: 'confusing', label: m.klai_assistant_feedback_type_confusing() },
    { value: 'missing', label: m.klai_assistant_feedback_type_missing() },
    { value: 'compliment', label: m.klai_assistant_feedback_type_compliment() },
  ]
  const [type, setType] = useState<FeedbackType>('idea')

  return (
    <IntakeForm
      endpoint="/api/app/assistant/feedback"
      minLength={3}
      label={m.klai_assistant_feedback_label()}
      placeholder={m.klai_assistant_feedback_placeholder()}
      submitLabel={m.klai_assistant_feedback_submit()}
      submittingLabel={m.klai_assistant_submitting()}
      successTitle={m.klai_assistant_feedback_success_title()}
      successDescription={m.klai_assistant_feedback_success_desc()}
      buildPayload={(rawText) => ({ raw_text: rawText, type, ...currentContext() })}
      controls={
        <ChipGroup>
          {feedbackTypes.map((item) => (
            <Button
              key={item.value}
              type="button"
              variant={type === item.value ? 'default' : 'secondary'}
              size="sm"
              onClick={() => setType(item.value)}
            >
              {item.label}
            </Button>
          ))}
        </ChipGroup>
      }
    />
  )
}

function ProblemView() {
  const severities: Array<{ value: ProblemSeverity; label: string }> = [
    { value: 'blocked', label: m.klai_assistant_problem_severity_blocked() },
    { value: 'workaround', label: m.klai_assistant_problem_severity_workaround() },
    { value: 'minor', label: m.klai_assistant_problem_severity_minor() },
  ]
  const [severity, setSeverity] = useState<ProblemSeverity>('workaround')

  return (
    <IntakeForm
      endpoint="/api/app/assistant/problem-reports"
      minLength={3}
      label={m.klai_assistant_problem_label()}
      placeholder={m.klai_assistant_problem_placeholder()}
      submitLabel={m.klai_assistant_problem_submit()}
      submittingLabel={m.klai_assistant_submitting()}
      successTitle={m.klai_assistant_problem_success_title()}
      successDescription={m.klai_assistant_problem_success_desc()}
      buildPayload={(rawText) => ({ raw_text: rawText, severity, ...currentContext() })}
      controls={
        <ChipGroup>
          {severities.map((item) => (
            <Button
              key={item.value}
              type="button"
              variant={severity === item.value ? 'default' : 'secondary'}
              size="sm"
              onClick={() => setSeverity(item.value)}
            >
              {item.label}
            </Button>
          ))}
        </ChipGroup>
      }
    />
  )
}

function ChatQuestionForm({
  placeholder,
  submitLabel,
}: {
  placeholder: string
  submitLabel: string
}) {
  const [value, setValue] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [chatConfig, setChatConfig] = useState<ChatConfig | null>(null)
  const [state, setState] = useState<'loading' | 'idle' | 'streaming' | 'error'>('loading')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const trimmed = value.trim()
  const canSubmit = trimmed.length >= 1 && state !== 'loading' && state !== 'streaming'

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, state])

  useEffect(() => {
    let cancelled = false

    async function loadConfig() {
      try {
        const cfg = await fetchKlaiHelpChatConfig()
        if (cancelled) return
        setChatConfig(cfg)
        setState('idle')
      } catch {
        if (!cancelled) setState('error')
      }
    }

    void loadConfig()

    return () => {
      cancelled = true
    }
  }, [])

  async function submit() {
    if (!canSubmit) return
    const content = trimmed
    const cfg = chatConfig
    if (!cfg) {
      setState('error')
      return
    }

    const userMessage: ChatMessage = { role: 'user', content }
    const nextMessages = [...messages, userMessage]
    setMessages([...nextMessages, { role: 'assistant', content: '' }])
    setValue('')
    setState('streaming')

    try {
      const postChat = (config: ChatConfig) =>
        fetch(config.chat_endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${config.session_token}`,
          },
          body: JSON.stringify({
            messages: nextMessages,
            stream: true,
          }),
        })

      let res = await postChat(cfg)
      if (res.status === 401) {
        const freshConfig = await fetchKlaiHelpChatConfig()
        setChatConfig(freshConfig)
        res = await postChat(freshConfig)
      }

      if (!res.ok || !res.body) throw new Error(`chat ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { value: chunk, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(chunk, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const data = line.slice(5).trim()
          if (!data || data === '[DONE]') continue
          try {
            const parsed = JSON.parse(data) as {
              choices?: Array<{
                delta?: { content?: string }
                message?: { content?: string }
              }>
              content?: string
            }
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
      setState('idle')
    } catch {
      setState('error')
      setMessages((prev) => prev.filter((message) => message.content || message.role === 'user'))
    }
  }

  function appendAssistantToken(token: string) {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (last?.role === 'assistant') {
        next[next.length - 1] = { ...last, content: last.content + token }
      }
      return next
    })
  }

  const hasMessages = messages.length > 0

  return (
    <div className="-mx-4 -my-4 flex min-h-[540px] flex-col bg-[var(--color-rl-bg)]">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
        {!hasMessages ? (
          <div className="flex min-h-[360px] flex-col items-center justify-center px-3 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-[14px] bg-[var(--color-rl-cream)] text-[var(--color-rl-dark)]">
              <MessageSquare className="h-8 w-8" strokeWidth={1.75} />
            </div>
            <h3 className="mt-7 text-base font-semibold leading-tight text-[var(--color-rl-dark)]">
              {m.klai_assistant_question_hero_title()}
            </h3>
            <p className="mt-2 max-w-sm text-[13px] leading-5 text-[var(--color-rl-dark)]/60">
              {m.klai_assistant_question_hero_desc()}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((message, index) => (
              <div
                key={index}
                className={cn(
                  'max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6',
                  message.role === 'user'
                    ? 'ml-auto bg-[var(--color-rl-accent)] text-[var(--color-rl-dark)]'
                    : 'mr-auto bg-[var(--color-rl-cream)] text-[var(--color-rl-dark)]',
                )}
              >
                {message.content || (
                  <span className="inline-flex gap-1">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:120ms]" />
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:240ms]" />
                  </span>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <form
        className="border-t border-[var(--color-rl-border)] bg-[var(--color-rl-bg)] px-3 py-3"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        {state === 'error' && (
          <p className="mb-3 text-sm text-[var(--color-destructive)]">
            {m.klai_assistant_error()}
          </p>
        )}
        <div className="flex items-end gap-3">
          <Textarea
            value={value}
            onChange={(event) => {
              setValue(event.target.value)
              if (state === 'error' && chatConfig) setState('idle')
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void submit()
              }
            }}
            placeholder={placeholder}
            rows={1}
            maxLength={4000}
            disabled={state === 'loading' || state === 'streaming'}
            aria-label={m.klai_assistant_question_label()}
            className="max-h-28 min-h-11 resize-none rounded-lg border-[var(--color-rl-border)] bg-[var(--color-rl-bg)] px-3 py-2.5 text-sm text-[var(--color-rl-dark)] placeholder:text-[var(--color-rl-dark)]/60 focus-visible:ring-[var(--color-rl-accent)]"
          />
          <Button
            type="submit"
            size="icon"
            disabled={!canSubmit}
            aria-label={submitLabel}
            className="h-11 w-11 shrink-0 rounded-lg bg-[var(--color-rl-accent)] text-[var(--color-rl-dark)] hover:bg-[var(--color-rl-accent-hover)]"
          >
            <Send className={cn('h-5 w-5', state === 'streaming' && 'animate-pulse')} />
            <span className="sr-only">
              {state === 'streaming' ? m.klai_assistant_submitting() : submitLabel}
            </span>
          </Button>
        </div>
        <p className="mt-2 px-3 text-center text-[11px] leading-5 text-[var(--color-rl-dark)]/40">
          {m.klai_assistant_disclaimer()}
        </p>
      </form>
    </div>
  )
}

function IntakeForm<TPayload extends IntakePayload>({
  endpoint,
  minLength,
  label,
  placeholder,
  submitLabel,
  submittingLabel,
  successTitle,
  successDescription,
  buildPayload,
  controls,
}: {
  endpoint: string
  minLength: number
  label: string
  placeholder: string
  submitLabel: string
  submittingLabel: string
  successTitle: string
  successDescription: string
  buildPayload: (rawText: string) => TPayload
  controls?: ReactNode
}) {
  const [value, setValue] = useState('')
  const [state, setState] = useState<SubmissionState>('idle')

  const trimmed = value.trim()
  const canSubmit = trimmed.length >= minLength && state !== 'submitting'

  async function submit() {
    if (!canSubmit) return
    setState('submitting')
    try {
      await apiFetch<{ ok: true }>(endpoint, {
        method: 'POST',
        body: JSON.stringify(buildPayload(trimmed)),
      })
      setState('submitted')
      setValue('')
    } catch {
      setState('error')
    }
  }

  if (state === 'submitted') {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-success)]/10 text-[var(--color-success)]">
          <CheckCircle2 className="h-6 w-6" />
        </div>
        <h3 className="mt-4 text-base font-semibold text-gray-900">
          {successTitle}
        </h3>
        <p className="mt-2 max-w-xs text-sm leading-6 text-gray-500">
          {successDescription}
        </p>
        <Button type="button" variant="secondary" className="mt-5" onClick={() => setState('idle')}>
          {m.klai_assistant_add_another()}
        </Button>
      </div>
    )
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      {controls}
      <div>
        <label className="text-sm font-medium text-gray-900" htmlFor={`${endpoint}-text`}>
          {label}
        </label>
        <Textarea
          id={`${endpoint}-text`}
          value={value}
          onChange={(event) => {
            setValue(event.target.value)
            if (state === 'error') setState('idle')
          }}
          placeholder={placeholder}
          rows={7}
          maxLength={4000}
          className="mt-2 min-h-40 resize-none"
        />
      </div>
      {state === 'error' && (
        <p className="text-sm text-[var(--color-destructive)]">
          {m.klai_assistant_error()}
        </p>
      )}
      <Button type="submit" disabled={!canSubmit} className="w-full">
        <Send className={cn('h-4 w-4', state === 'submitting' && 'animate-pulse')} />
        {state === 'submitting' ? submittingLabel : submitLabel}
      </Button>
    </form>
  )
}

function ChipGroup({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap gap-2" role="group">
      {children}
    </div>
  )
}
