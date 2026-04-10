import { useState } from 'react'
import { Tooltip } from '@/components/ui/tooltip'
import { Input } from '@/components/ui/input'
import {
  Loader2,
  Pencil,
  Check,
  X,
  Trash2,
  Copy,
  CheckCheck,
  Mic,
  Download,
  Video,
  Square,
  FileText,
  RotateCcw,
} from 'lucide-react'
import * as m from '@/paraglide/messages'
import type { UnifiedItem, Source } from '../_types'

const ACTIVE_MEETING_STATUSES = ['pending', 'joining', 'recording', 'stopping', 'processing']

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function languageToCountryCode(lang: string): string {
  const map: Record<string, string> = {
    nl: 'nl', en: 'gb', de: 'de', fr: 'fr', es: 'es',
    it: 'it', pt: 'pt', pl: 'pl', ru: 'ru', tr: 'tr',
    ar: 'sa', zh: 'cn', ja: 'jp', ko: 'kr', sv: 'se',
    da: 'dk', no: 'no', fi: 'fi', cs: 'cz', hu: 'hu',
    ro: 'ro', uk: 'ua',
  }
  return map[lang.toLowerCase()] ?? lang.toLowerCase()
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('nl-NL', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function StatusBadge({ status, source }: { status: string; source: Source }) {
  const config: Record<string, { label: string; classes: string }> = {
    pending:    { label: m.app_meetings_status_pending(),    classes: 'bg-[var(--color-rl-accent)]/10 text-[var(--color-rl-accent)]' },
    joining:    { label: m.app_meetings_status_joining(),    classes: 'bg-[var(--color-rl-accent)]/10 text-[var(--color-rl-accent)] animate-pulse' },
    recording:  { label: m.app_meetings_status_recording(),  classes: 'bg-[var(--color-destructive)]/10 text-[var(--color-destructive)] animate-pulse' },
    processing: { label: source === 'upload' ? m.app_transcribe_status_processing() : m.app_meetings_status_processing(), classes: 'bg-[var(--color-rl-accent)]/10 text-[var(--color-rl-accent)] animate-pulse' },
    done:       { label: m.app_meetings_status_done(),       classes: 'bg-[var(--color-success)]/10 text-[var(--color-success)]' },
    failed:     { label: m.app_transcribe_status_failed(),   classes: 'bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]' },
  }
  const c = config[status] ?? { label: status, classes: 'bg-[var(--color-rl-cream)] text-[var(--color-foreground)]' }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${c.classes}`}>
      {c.label}
    </span>
  )
}

function MetaText({ item }: { item: UnifiedItem }) {
  const parts: string[] = []
  if (item.text) {
    parts.push(`${item.text.trim().split(/\s+/).filter(Boolean).length.toLocaleString()} words`)
  }
  if (item.duration_seconds != null) parts.push(formatDuration(item.duration_seconds))
  parts.push(
    item.source === 'upload'
      ? m.app_transcribe_source_audio()
      : m.app_transcribe_source_meeting(),
  )

  return (
    <span className="text-xs text-[var(--color-muted-foreground)]">
      {item.language && (
        <>
          <img
            src={`https://flagcdn.com/16x12/${languageToCountryCode(item.language)}.png`}
            width="16"
            height="12"
            alt={item.language.toUpperCase()}
            className="inline-block rounded-sm mr-1 align-text-bottom"
          />
          <span className="mr-0.5">{item.language.toUpperCase()}</span>
          {parts.length > 0 && <span className="mx-1">&middot;</span>}
        </>
      )}
      {parts.join(' \u00b7 ')}
    </span>
  )
}

interface TranscriptionTableProps {
  allItems: UnifiedItem[]
  filteredItems: UnifiedItem[]
  search: string
  onSearchChange: (value: string) => void
  onNavigateToDetail: (item: UnifiedItem) => void
  onRename: (id: string, name: string | null) => void
  isRenaming: boolean
  renamingId?: string
  onDeleteUpload: (id: string) => void
  isDeletingUpload: boolean
  deletingUploadId?: string
  onDeleteMeeting: (id: string) => void
  isDeletingMeeting: boolean
  deletingMeetingId?: string
  onStop: (id: string) => void
  isStopping: boolean
  stoppingId?: string
  onRetry: (id: string) => void
  isRetrying: boolean
  retryingId?: string
}

export function TranscriptionTable({
  allItems,
  filteredItems,
  search,
  onSearchChange,
  onNavigateToDetail,
  onRename,
  isRenaming,
  renamingId,
  onDeleteUpload,
  isDeletingUpload,
  deletingUploadId,
  onDeleteMeeting,
  isDeletingMeeting,
  deletingMeetingId,
  onStop,
  isStopping,
  stoppingId,
  onRetry,
  isRetrying,
  retryingId,
}: TranscriptionTableProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState<string>('')
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  function startEdit(item: UnifiedItem) {
    setConfirmingDeleteId(null)
    setEditingId(item.id)
    setEditName(item.title ?? '')
  }

  function cancelEdit() {
    setEditingId(null)
    setEditName('')
  }

  function saveEdit(id: string) {
    onRename(id, editName.trim() || null)
    setEditingId(null)
  }

  function downloadText(item: UnifiedItem) {
    if (!item.text) return
    const blob = new Blob([item.text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${item.title ?? 'transcriptie'}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function copyText(item: UnifiedItem) {
    if (!item.text) return
    await navigator.clipboard.writeText(item.text)
    setCopiedId(item.id)
    setTimeout(() => setCopiedId((prev) => (prev === item.id ? null : prev)), 2000)
  }

  function handleDelete(item: UnifiedItem) {
    setConfirmingDeleteId(null)
    if (item.source === 'upload') {
      onDeleteUpload(item.id)
    } else {
      onDeleteMeeting(item.id)
    }
  }

  if (allItems.length === 0) {
    return (
      <div data-help-id="transcribe-list" className="flex flex-col items-center gap-3 py-16 text-center">
        <Mic className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
        <div className="space-y-1">
          <p className="font-medium text-[var(--color-foreground)]">
            {m.app_transcribe_empty_heading()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.app_transcribe_empty_body()}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div data-help-id="transcribe-list">
      <div className="pb-5">
        <Input
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={m.app_transcribe_search_placeholder()}
          className="h-8 text-sm max-w-xs"
        />
      </div>

      {filteredItems.length === 0 ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.app_transcribe_search_empty()}
        </p>
      ) : (
        <table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                {m.app_transcribe_col_text()}
              </th>
              <th className="py-3 pr-2 text-right text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-28">
                {m.app_transcribe_col_date()}
              </th>
              <th className="py-3 text-right text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-32" />
            </tr>
          </thead>
          <tbody>
            {filteredItems.map((item) => {
              const isEditing = editingId === item.id
              const isConfirmingDelete = confirmingDeleteId === item.id
              const isSaving = isRenaming && renamingId === item.id
              const isDeleting =
                (isDeletingUpload && deletingUploadId === item.id) ||
                (isDeletingMeeting && deletingMeetingId === item.id)
              const isCopied = copiedId === item.id
              const isActive = item.source === 'meeting' && ACTIVE_MEETING_STATUSES.includes(item.status)
              const isItemStopping = isStopping && stoppingId === item.id
              const isFailed = item.source === 'upload' && item.status === 'failed'
              const isItemRetrying = isRetrying && retryingId === item.id

              return (
                <tr
                  key={`${item.source}-${item.id}`}
                  className="border-b border-[var(--color-border)] last:border-b-0"
                >
                  {/* Title + preview + metadata */}
                  <td className="py-4 pr-4 align-top">
                    {isEditing ? (
                      <div className="flex items-center gap-2">
                        <Input
                          value={editName}
                          onChange={(e) => setEditName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') saveEdit(item.id)
                            if (e.key === 'Escape') cancelEdit()
                          }}
                          disabled={isSaving}
                          autoFocus
                          className="h-7 text-sm max-w-sm"
                        />
                        {isSaving ? (
                          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                        ) : (
                          <>
                            <button
                              onClick={() => saveEdit(item.id)}
                              aria-label={m.app_transcribe_edit_save()}
                              className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-success)] text-white transition-colors hover:opacity-90"
                            >
                              <Check className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={cancelEdit}
                              aria-label={m.app_transcribe_edit_cancel()}
                              className="flex h-7 w-7 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-border)]"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    ) : (
                      <div className="flex items-start gap-2.5">
                        {/* Source icon */}
                        <Tooltip
                          label={
                            item.source === 'upload'
                              ? m.app_transcribe_source_audio()
                              : m.app_transcribe_source_meeting()
                          }
                        >
                          {item.source === 'upload' ? (
                            <Mic className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-muted-foreground)]" />
                          ) : (
                            <Video className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-muted-foreground)]" />
                          )}
                        </Tooltip>

                        <div className="min-w-0 flex-1">
                          {/* Title line */}
                          <div className="flex items-center gap-2">
                            {item.title ? (
                              item.status === 'done' ? (
                                <button
                                  type="button"
                                  className="truncate font-medium text-left text-[var(--color-foreground)] hover:underline cursor-pointer"
                                  onClick={() => onNavigateToDetail(item)}
                                >
                                  {item.title}
                                </button>
                              ) : (
                                <span className="truncate font-medium text-[var(--color-foreground)]">
                                  {item.title}
                                </span>
                              )
                            ) : (
                              <span className="truncate text-[var(--color-muted-foreground)]">
                                {item.meeting_url ?? '\u2014'}
                              </span>
                            )}
                            {item.source === 'upload' && item.has_summary && (
                              <Tooltip label={m.app_transcribe_has_summary()}>
                                <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)]" />
                              </Tooltip>
                            )}
                            {item.status !== 'done' && (
                              <StatusBadge status={item.status} source={item.source} />
                            )}
                          </div>

                          {/* Preview text */}
                          {item.text && item.title && (
                            <p className="mt-0.5 truncate text-[var(--color-muted-foreground)]">
                              {item.text}
                            </p>
                          )}

                          {/* Metadata line */}
                          <div className="mt-1">
                            <MetaText item={item} />
                          </div>
                        </div>
                      </div>
                    )}
                  </td>

                  {/* Date */}
                  <td className="py-4 pr-2 align-top text-right whitespace-nowrap w-28">
                    <span className="text-sm text-[var(--color-muted-foreground)] tabular-nums">
                      {formatDate(item.created_at)}
                    </span>
                  </td>

                  {/* Actions - always visible, muted until hover */}
                  <td className="py-4 align-top text-right w-32">
                    {isEditing ? null : isConfirmingDelete ? (
                      <div className="flex items-center justify-end gap-1">
                        {isDeleting ? (
                          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                        ) : (
                          <>
                            <button
                              onClick={() => handleDelete(item)}
                              aria-label={m.app_transcribe_delete_confirm()}
                              className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-destructive)] text-white transition-colors hover:opacity-90"
                            >
                              <Check className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={() => setConfirmingDeleteId(null)}
                              aria-label={m.app_transcribe_delete_cancel()}
                              className="flex h-7 w-7 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-border)]"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    ) : (
                      <div className="flex items-center justify-end gap-1">
                        {/* Rename */}
                        <Tooltip label={m.app_transcribe_edit_label()}>
                          <button
                            onClick={() => startEdit(item)}
                            aria-label={m.app_transcribe_edit_label()}
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                        </Tooltip>

                        {/* Active meeting: stop */}
                        {isActive && (
                          <Tooltip label={m.app_meetings_stop_button()}>
                            <button
                              onClick={() => onStop(item.id)}
                              disabled={isItemStopping}
                              aria-label={m.app_meetings_stop_button()}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                            >
                              {isItemStopping ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Square className="h-3.5 w-3.5" />
                              )}
                            </button>
                          </Tooltip>
                        )}

                        {/* Retry failed */}
                        {isFailed && (
                          <Tooltip label={m.app_transcribe_retry_button()}>
                            <button
                              onClick={() => onRetry(item.id)}
                              disabled={isItemRetrying}
                              aria-label={m.app_transcribe_retry_button()}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-rl-accent)] transition-opacity hover:opacity-70"
                            >
                              {isItemRetrying ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <RotateCcw className="h-3.5 w-3.5" />
                              )}
                            </button>
                          </Tooltip>
                        )}

                        {/* Copy */}
                        {item.text && (
                          <Tooltip label={m.app_transcribe_copy_label()}>
                            <button
                              data-help-id="transcribe-copy"
                              onClick={() => void copyText(item)}
                              aria-label={m.app_transcribe_copy_label()}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70"
                            >
                              {isCopied ? (
                                <CheckCheck className="h-3.5 w-3.5 text-[var(--color-success)]" />
                              ) : (
                                <Copy className="h-3.5 w-3.5" />
                              )}
                            </button>
                          </Tooltip>
                        )}

                        {/* Download */}
                        {item.text && (
                          <Tooltip label={m.app_transcribe_download_label()}>
                            <button
                              data-help-id="transcribe-download"
                              onClick={() => downloadText(item)}
                              aria-label={m.app_transcribe_download_label()}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-success)] transition-opacity hover:opacity-70"
                            >
                              <Download className="h-3.5 w-3.5" />
                            </button>
                          </Tooltip>
                        )}

                        {/* Delete */}
                        <Tooltip label={m.app_transcribe_delete_label()}>
                          <button
                            onClick={() => { cancelEdit(); setConfirmingDeleteId(item.id) }}
                            aria-label={m.app_transcribe_delete_label()}
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </Tooltip>
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
