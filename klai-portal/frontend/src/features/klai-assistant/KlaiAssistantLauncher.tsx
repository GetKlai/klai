import { useState, type ReactNode } from 'react'
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

interface AssistantContextPayload {
  page_url: string
  route_id?: string
  locale: string
  viewport: string
}

interface IntakePayload extends AssistantContextPayload {
  raw_text: string
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
          className="h-12 w-12 shadow-lg"
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
      className="fixed bottom-20 right-4 z-[10002] flex max-h-[min(720px,calc(100vh-7rem))] w-[calc(100vw-2rem)] max-w-[420px] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl sm:right-6"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          {showBack && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => onModeChange('home')}
              aria-label={m.klai_assistant_back()}
              className="h-8 w-8 shrink-0"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-900 text-white">
            <Sparkles className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-gray-900">
              {m.klai_assistant_title()}
            </h2>
            <p className="truncate text-[11px] text-gray-400">
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
          className="h-8 w-8 shrink-0"
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
          className="h-auto w-full justify-start rounded-xl px-3 py-3 text-left"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-700">
            <option.icon className="h-4 w-4" />
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-gray-900">
              {option.title}
            </span>
            <span className="mt-0.5 block whitespace-normal text-xs font-normal leading-5 text-gray-500">
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
    <IntakeForm
      endpoint="/api/app/assistant/questions"
      minLength={3}
      label={m.klai_assistant_question_label()}
      placeholder={m.klai_assistant_question_placeholder()}
      submitLabel={m.klai_assistant_question_submit()}
      submittingLabel={m.klai_assistant_submitting()}
      successTitle={m.klai_assistant_question_success_title()}
      successDescription={m.klai_assistant_question_success_desc()}
      buildPayload={(rawText) => ({ raw_text: rawText, ...currentContext() })}
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
