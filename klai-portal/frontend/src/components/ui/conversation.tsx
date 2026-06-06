import * as React from 'react'
import { ArrowLeft, Check, Loader2, Pencil, Send, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InlineRowButton } from '@/components/ui/inline-row-button'
import { Textarea } from '@/components/ui/textarea'
import * as m from '@/paraglide/messages'

// Owned conversation surface. Single calm Klai visual language shared by the
// account "Mijn meldingen" conversation and the "Berichten" tab (and reusable
// by admin). One message timeline: grouped by sender + day, quiet system lines
// for status changes, and a composer with Cmd/Enter to send. Do not hand-roll
// chat bubbles in a page again — extend this component.

export interface ConversationMessageEntry {
  type?: 'message'
  id: string | number
  /** `me` = the current viewer (right), `them` = the other party (left). */
  side: 'me' | 'them'
  author: string
  body: string
  at: string
  /** When true and `onEditMessage` is provided, the bubble shows an edit affordance. */
  editable?: boolean
}

export interface ConversationSystemEntry {
  type: 'system'
  id: string | number
  label: string
  at: string
}

export type ConversationEntry = ConversationMessageEntry | ConversationSystemEntry

function isSystem(entry: ConversationEntry): entry is ConversationSystemEntry {
  return entry.type === 'system'
}

function startOfDay(value: string): number {
  const d = new Date(value)
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

function formatTime(value: string, locale: 'nl' | 'en'): string {
  return new Intl.DateTimeFormat(locale === 'nl' ? 'nl-NL' : 'en-US', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function formatDaySeparator(value: string, locale: 'nl' | 'en'): string {
  const day = startOfDay(value)
  const today = startOfDay(new Date().toISOString())
  const oneDay = 24 * 60 * 60 * 1000
  if (day === today) return m.account_conversation_today()
  if (day === today - oneDay) return m.account_conversation_yesterday()
  const now = new Date()
  return new Intl.DateTimeFormat(locale === 'nl' ? 'nl-NL' : 'en-US', {
    day: 'numeric',
    month: 'short',
    ...(new Date(value).getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  }).format(new Date(value))
}

interface MessageBody {
  id: string | number
  body: string
  editable?: boolean
}

interface MessageGroup {
  side: 'me' | 'them'
  author: string
  at: string
  bodies: MessageBody[]
}

/** Build day buckets, each with sender-grouped message runs and system lines. */
function buildLayout(entries: ConversationEntry[]) {
  const sorted = [...entries].sort((a, b) => {
    const ta = new Date(a.at).getTime()
    const tb = new Date(b.at).getTime()
    if (ta !== tb) return ta - tb
    return String(a.id).localeCompare(String(b.id))
  })

  const days: { day: number; at: string; blocks: (MessageGroup | ConversationSystemEntry)[] }[] = []
  for (const entry of sorted) {
    const day = startOfDay(entry.at)
    let bucket = days.at(-1)
    if (!bucket || bucket.day !== day) {
      bucket = { day, at: entry.at, blocks: [] }
      days.push(bucket)
    }
    if (isSystem(entry)) {
      bucket.blocks.push(entry)
      continue
    }
    const body: MessageBody = {
      id: entry.id,
      body: entry.body,
      editable: entry.editable,
    }
    const last = bucket.blocks.at(-1)
    if (last && !('label' in last) && last.side === entry.side && last.author === entry.author) {
      last.bodies.push(body)
    } else {
      bucket.blocks.push({
        side: entry.side,
        author: entry.author,
        at: entry.at,
        bodies: [body],
      })
    }
  }
  return days
}

export function ConversationTimeline({
  entries,
  locale,
  loading = false,
  emptyLabel,
  autoScroll = true,
  onEditMessage,
  className,
}: {
  entries: ConversationEntry[]
  locale: 'nl' | 'en'
  loading?: boolean
  emptyLabel?: string
  autoScroll?: boolean
  /** When provided, editable own messages show an inline edit affordance. */
  onEditMessage?: (id: string | number, body: string) => void
  className?: string
}) {
  const bottomRef = React.useRef<HTMLDivElement | null>(null)
  const count = entries.length
  const [editingId, setEditingId] = React.useState<string | number | null>(null)
  const [editDraft, setEditDraft] = React.useState('')

  const startEdit = (id: string | number, body: string) => {
    setEditingId(id)
    setEditDraft(body)
  }
  const cancelEdit = () => setEditingId(null)
  const saveEdit = (id: string | number) => {
    const trimmed = editDraft.trim()
    if (trimmed.length > 0) onEditMessage?.(id, trimmed)
    setEditingId(null)
  }

  React.useEffect(() => {
    const node = bottomRef.current
    if (autoScroll && count > 0 && typeof node?.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'nearest' })
    }
  }, [autoScroll, count])

  if (loading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center gap-2 text-sm text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        {m.admin_shared_loading()}
      </div>
    )
  }

  if (count === 0) {
    return (
      <div className="flex min-h-[160px] items-center justify-center px-4 text-center text-sm text-gray-400">
        {emptyLabel ?? ''}
      </div>
    )
  }

  const days = buildLayout(entries)

  return (
    <div className={cn('space-y-5', className)}>
      {days.map((bucket) => (
        <div key={bucket.day} className="space-y-4">
          <p className="text-center text-xs text-gray-400">
            {formatDaySeparator(bucket.at, locale)}
          </p>

          {bucket.blocks.map((block, index) =>
            'label' in block ? (
              <div key={`sys-${block.id}`} className="flex justify-center">
                <span className="rounded-full bg-[var(--color-secondary)] px-3 py-1 text-xs text-gray-500">
                  {block.label}
                </span>
              </div>
            ) : (
              <MessageGroupView
                key={`grp-${block.side}-${index}-${block.bodies[0]?.id}`}
                group={block}
                locale={locale}
                canEdit={!!onEditMessage}
                editingId={editingId}
                editDraft={editDraft}
                onEditDraftChange={setEditDraft}
                onStartEdit={startEdit}
                onCancelEdit={cancelEdit}
                onSaveEdit={saveEdit}
              />
            ),
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

// Shared bubble geometry. The real bubble, and the ghost that sizes the inline
// editor, MUST use the exact same text-box classes — keeping them in one place
// is what makes the editor truly zero-shift (and keeps them from drifting).
const BUBBLE_TEXT = 'whitespace-pre-wrap break-words px-3.5 py-2 text-sm leading-relaxed'
const ME_BUBBLE = 'rounded-2xl rounded-br-md bg-[var(--color-secondary)] text-[var(--color-foreground)]'
const THEM_BUBBLE = 'rounded-2xl rounded-bl-md border border-[var(--color-border)] bg-white text-[var(--color-foreground)]'

/**
 * Inline message editor with zero layout shift (ui-standards "Inline Edit").
 * An invisible ghost copy of the text defines the exact same box the bubble
 * occupied (shrink-to-fit width, identical wrap and height); the textarea is
 * painted absolutely on top. Editing never moves the message — only the
 * Save/Cancel row is added below. The ghost grows with the draft, so the box
 * keeps matching as you type.
 */
function MessageEditor({
  value,
  onChange,
  onSave,
  onCancel,
}: {
  value: string
  onChange: (value: string) => void
  onSave: () => void
  onCancel: () => void
}) {
  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault()
      onSave()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      onCancel()
    }
  }
  return (
    <div className="flex max-w-[85%] flex-col items-end gap-1 self-end">
      <div
        className={cn(
          'relative max-h-[60vh] overflow-hidden focus-within:ring-2 focus-within:ring-[var(--color-ring)]',
          ME_BUBBLE,
        )}
      >
        {/* Ghost: invisible, defines the box. Trailing ZWSP keeps a final empty
            line from collapsing the height. */}
        <div aria-hidden className={cn(BUBBLE_TEXT, 'invisible')}>
          {value + '​'}
        </div>
        <textarea
          value={value}
          maxLength={4000}
          autoFocus
          aria-label={m.account_conversation_edit()}
          className={cn(
            BUBBLE_TEXT,
            'absolute inset-0 resize-none overflow-y-auto border-0 bg-transparent text-[var(--color-foreground)] outline-none',
          )}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>
      <div className="flex justify-end gap-2">
        <InlineRowButton tone="success" onClick={onSave}>
          <Check />
          {m.admin_shared_save()}
        </InlineRowButton>
        <InlineRowButton onClick={onCancel}>
          <X />
          {m.admin_users_cancel()}
        </InlineRowButton>
      </div>
    </div>
  )
}

function MessageGroupView({
  group,
  locale,
  canEdit,
  editingId,
  editDraft,
  onEditDraftChange,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
}: {
  group: MessageGroup
  locale: 'nl' | 'en'
  canEdit: boolean
  editingId: string | number | null
  editDraft: string
  onEditDraftChange: (value: string) => void
  onStartEdit: (id: string | number, body: string) => void
  onCancelEdit: () => void
  onSaveEdit: (id: string | number) => void
}) {
  const isMe = group.side === 'me'
  return (
    <div className={cn('flex flex-col gap-1', isMe ? 'items-end' : 'items-start')}>
      <p className="px-1 text-xs text-gray-400">
        {group.author} · {formatTime(group.at, locale)}
      </p>
      {group.bodies.map((message) => {
        const showEdit = canEdit && isMe && message.editable
        if (editingId === message.id) {
          return (
            <MessageEditor
              key={message.id}
              value={editDraft}
              onChange={onEditDraftChange}
              onSave={() => onSaveEdit(message.id)}
              onCancel={onCancelEdit}
            />
          )
        }
        return (
          <div
            key={message.id}
            className={cn('group/bubble relative max-w-[85%]', isMe ? 'self-end' : 'self-start')}
          >
            {showEdit && (
              // Absolutely positioned so it never consumes flow width — the bubble
              // stays exactly as wide as the editor that replaces it (no reflow).
              <button
                type="button"
                onClick={() => onStartEdit(message.id, message.body)}
                aria-label={m.account_conversation_edit()}
                title={m.account_conversation_edit()}
                className="absolute right-full top-1/2 mr-1.5 -translate-y-1/2 text-gray-300 opacity-0 transition-opacity hover:text-gray-600 group-hover/bubble:opacity-100"
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
            )}
            <div className={cn(BUBBLE_TEXT, isMe ? ME_BUBBLE : THEM_BUBBLE)}>
              {message.body}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function ConversationComposer({
  value,
  onChange,
  onSubmit,
  isSubmitting = false,
  disabled = false,
  placeholder,
  sendLabel,
  textareaId,
  maxLength = 4000,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  isSubmitting?: boolean
  disabled?: boolean
  placeholder?: string
  sendLabel: string
  textareaId?: string
  maxLength?: number
}) {
  const canSend = value.trim().length > 0 && !isSubmitting && !disabled

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault()
      if (canSend) onSubmit()
    }
  }

  return (
    <div className="space-y-2 border-t border-gray-200 pt-4">
      <Textarea
        id={textareaId}
        rows={3}
        value={value}
        maxLength={maxLength}
        placeholder={placeholder}
        disabled={disabled || isSubmitting}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-gray-400">{m.account_conversation_send_hint()}</span>
        <Button type="button" disabled={!canSend} onClick={onSubmit}>
          {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {sendLabel}
        </Button>
      </div>
    </div>
  )
}

/**
 * Full conversation detail surface: header (title + optional badge/actions, with
 * the back action on the right per ui-standards "Back Actions"), the timeline,
 * and the composer. Shared by account "Mijn meldingen"/"Berichten" and the
 * platform-admin messages tab so the master→detail-with-back flow is identical.
 */
export function ConversationPanel({
  title,
  subtitle,
  badge,
  headerActions,
  entries,
  loading = false,
  locale,
  emptyLabel,
  draft,
  onDraftChange,
  onSend,
  isSending = false,
  composerDisabled = false,
  placeholder,
  sendLabel,
  onEditMessage,
  onBack,
}: {
  title: string
  subtitle?: string
  badge?: React.ReactNode
  headerActions?: React.ReactNode
  entries: ConversationEntry[]
  loading?: boolean
  locale: 'nl' | 'en'
  emptyLabel?: string
  draft: string
  onDraftChange: (value: string) => void
  onSend: () => void
  isSending?: boolean
  composerDisabled?: boolean
  placeholder?: string
  sendLabel: string
  onEditMessage?: (id: string | number, body: string) => void
  onBack: () => void
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-display-bold text-gray-900">{title}</h2>
            {badge}
          </div>
          {subtitle && <p className="mt-1 text-xs text-gray-400">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {headerActions}
          <Button type="button" variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.account_conversation_back()}
          </Button>
        </div>
      </div>

      <ConversationTimeline
        entries={entries}
        locale={locale}
        loading={loading}
        emptyLabel={emptyLabel}
        onEditMessage={onEditMessage}
      />

      <ConversationComposer
        value={draft}
        onChange={onDraftChange}
        onSubmit={onSend}
        isSubmitting={isSending}
        disabled={composerDisabled}
        placeholder={placeholder}
        sendLabel={sendLabel}
      />
    </div>
  )
}
