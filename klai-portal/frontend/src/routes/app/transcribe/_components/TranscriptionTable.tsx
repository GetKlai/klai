import { useState, useEffect, useRef } from 'react'
import { Badge } from '@/components/ui/badge'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { InlineEditRow } from '@/components/ui/inline-edit-row'
import {
  ListFrame,
  ListHeader,
  ListRow,
  ListRowActions,
  ListRowContent,
  ListRowDescription,
  ListRowTitle,
} from '@/components/ui/list'
import { ListEmptyState } from '@/components/ui/list-state'
import {
  BorderedRowActionIconButton,
  RowActionGroup,
} from '@/components/ui/row-action'
import { SearchInput } from '@/components/ui/search-input'
import {
  Loader2,
  CheckCheck,
  Mic,
  Video,
  FileText,
} from 'lucide-react'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
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
  return new Date(dateStr).toLocaleDateString(getLocale(), {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function StatusBadge({ status, source }: { status: string; source: Source }) {
  const config: Record<string, { label: string; variant: 'secondary' | 'success' | 'destructive' | 'info'; className?: string }> = {
    pending:    { label: m.app_meetings_status_pending(),    variant: 'info' },
    joining:    { label: m.app_meetings_status_joining(),    variant: 'info', className: 'animate-pulse' },
    recording:  { label: m.app_meetings_status_recording(),  variant: 'destructive', className: 'animate-pulse' },
    processing: { label: source === 'upload' ? m.app_transcribe_status_processing() : m.app_meetings_status_processing(), variant: 'info', className: 'animate-pulse' },
    done:       { label: m.app_meetings_status_done(),       variant: 'success' },
    failed:     { label: m.app_transcribe_status_failed(),   variant: 'destructive' },
  }
  const c = config[status] ?? { label: status, variant: 'secondary' as const }
  return <Badge variant={c.variant} className={c.className}>{c.label}</Badge>
}

function metaParts(item: UnifiedItem) {
  const parts: string[] = [
    item.source === 'upload'
      ? String(m.app_transcribe_source_audio())
      : String(m.app_transcribe_source_meeting()),
  ]
  return parts
}

function factParts(item: UnifiedItem) {
  const parts: string[] = []
  if (item.text) {
    const count = item.text.trim().split(/\s+/).filter(Boolean).length
    parts.push(m.app_transcribe_meta_word_count({ count: count.toLocaleString(getLocale()) }))
  }
  if (item.duration_seconds != null) parts.push(formatDuration(item.duration_seconds))
  return parts
}

function metaTextValue(item: UnifiedItem) {
  const parts = metaParts(item)
  if (item.language) parts.unshift(item.language.toUpperCase())
  return parts.join(' \u00b7 ')
}

function editDescriptionValue(item: UnifiedItem) {
  return [metaTextValue(item), factParts(item).join(' \u00b7 ')].filter(Boolean).join(' \u00b7 ')
}

function MetaText({ item }: { item: UnifiedItem }) {
  const parts = metaParts(item)

  return (
    <span className="text-xs text-gray-400">
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

function FactText({ item }: { item: UnifiedItem }) {
  const parts = factParts(item)
  return (
    <span className="text-sm text-gray-500">
      {parts.length > 0 ? parts.join(' \u00b7 ') : '\u2014'}
    </span>
  )
}

function sourceLabel(item: UnifiedItem) {
  return item.source === 'upload'
    ? m.app_transcribe_source_audio()
    : m.app_transcribe_source_meeting()
}

const transcriptionListGrid =
  'grid-cols-[2rem_minmax(0,1fr)] lg:grid-cols-[2rem_minmax(0,1fr)_minmax(7rem,0.48fr)_minmax(7rem,0.45fr)_192px]'

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
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const wasRenaming = useRef(false)

  // Close edit mode when the rename mutation completes (success or error)
  useEffect(() => {
    if (wasRenaming.current && !isRenaming) {
      setEditingId(null)
    }
    wasRenaming.current = isRenaming
  }, [isRenaming])

  function startEdit(item: UnifiedItem) {
    setConfirmingDeleteId(null)
    setEditingId(item.id)
  }

  function cancelEdit() {
    setEditingId(null)
  }

  function saveEdit(id: string, name: string) {
    onRename(id, name.trim() || null)
    // Edit closes via useEffect when isRenaming transitions false
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
      <ListFrame data-help-id="transcribe-list">
        <ListEmptyState
          title={m.app_transcribe_empty_heading()}
          description={m.app_transcribe_empty_body()}
        />
      </ListFrame>
    )
  }

  return (
    <div data-help-id="transcribe-list" className="space-y-5">
      <div className="max-w-sm">
        <SearchInput
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={m.app_transcribe_search_placeholder()}
          aria-label={m.app_transcribe_search_placeholder()}
        />
      </div>

      {filteredItems.length === 0 ? (
        <ListFrame>
          <ListEmptyState title={m.app_transcribe_search_empty()} />
        </ListFrame>
      ) : (
        <ListFrame>
          <ListHeader className={`hidden gap-x-3 ${transcriptionListGrid} lg:grid`}>
            <span>{m.app_transcribe_col_source()}</span>
            <span>{m.app_transcribe_col_text()}</span>
            <span>{m.app_transcribe_col_words()} / {m.app_transcribe_col_duration()}</span>
            <span>{m.app_transcribe_col_date()}</span>
            <span className="justify-self-stretch text-right">{m.app_transcribe_col_actions()}</span>
          </ListHeader>

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
            const canOpen = item.status === 'done'

            return (
              <ListRow
                key={`${item.source}-${item.id}`}
                interactive={canOpen && !isEditing}
                confirming={isConfirmingDelete}
                onClick={canOpen && !isEditing ? () => onNavigateToDetail(item) : undefined}
                className={`grid items-center gap-x-3 gap-y-3 px-4 py-4 ${transcriptionListGrid}`}
              >
                <div
                  className="flex h-8 w-8 items-center justify-center rounded-md text-gray-400"
                  aria-label={sourceLabel(item)}
                  title={sourceLabel(item)}
                >
                  {item.source === 'upload' ? (
                    <Mic className="h-4 w-4" />
                  ) : (
                    <Video className="h-4 w-4" />
                  )}
                </div>

                {isEditing ? (
                  <div className="col-start-2 min-w-0 lg:col-span-4">
                    <InlineEditRow
                      isEditing
                      value={item.title ?? ''}
                      description={editDescriptionValue(item)}
                      isSaving={isSaving}
                      saveLabel={m.app_transcribe_edit_save()}
                      cancelLabel={m.app_transcribe_edit_cancel()}
                      onSubmit={({ name }) => saveEdit(item.id, name)}
                      onCancel={cancelEdit}
                    />
                  </div>
                ) : (
                  <>
                    <ListRowContent>
                      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                        {item.title ? (
                          <ListRowTitle className={canOpen ? 'group-hover:underline' : undefined}>
                            {item.title}
                          </ListRowTitle>
                        ) : (
                          <ListRowTitle className="text-gray-400">
                            {item.meeting_url ?? '\u2014'}
                          </ListRowTitle>
                        )}
                        {item.source === 'upload' && item.has_summary && (
                          <FileText
                            className="h-3.5 w-3.5 shrink-0 text-gray-400"
                            aria-label={m.app_transcribe_has_summary()}
                          />
                        )}
                        {item.status !== 'done' && (
                          <StatusBadge status={item.status} source={item.source} />
                        )}
                      </div>
                      <ListRowDescription>
                        <MetaText item={item} />
                      </ListRowDescription>
                    </ListRowContent>

                    <div className="col-start-2 whitespace-nowrap tabular-nums lg:col-start-auto">
                      <FactText item={item} />
                    </div>

                    <div className="col-start-2 whitespace-nowrap text-sm text-gray-900 tabular-nums lg:col-start-auto">
                      {formatDate(item.created_at)}
                    </div>

                    <ListRowActions
                      className="col-start-2 self-center justify-self-end lg:col-start-auto"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <InlineDeleteConfirm
                        isConfirming={isConfirmingDelete}
                        isPending={isDeleting}
                        label={m.app_transcribe_delete_confirm_name({ name: item.title ?? '' })}
                        cancelLabel={m.app_transcribe_delete_cancel()}
                        onConfirm={() => handleDelete(item)}
                        onCancel={() => setConfirmingDeleteId(null)}
                      >
                        <RowActionGroup>
                          <BorderedRowActionIconButton
                            label={m.app_transcribe_edit_label()}
                            action="edit"
                            onClick={() => startEdit(item)}
                          />

                          {isActive && (
                            <BorderedRowActionIconButton
                              label={m.app_meetings_stop_button()}
                              action="stop"
                              disabled={isItemStopping}
                              spinner={isItemStopping ? <Loader2 className="animate-spin" /> : undefined}
                              onClick={() => onStop(item.id)}
                            />
                          )}

                          {isFailed && (
                            <BorderedRowActionIconButton
                              label={m.app_transcribe_retry_button()}
                              action="retry"
                              disabled={isItemRetrying}
                              spinner={isItemRetrying ? <Loader2 className="animate-spin" /> : undefined}
                              onClick={() => onRetry(item.id)}
                            />
                          )}

                          {item.text && (
                            <BorderedRowActionIconButton
                              data-help-id="transcribe-copy"
                              label={m.app_transcribe_copy_label()}
                              action="copy"
                              icon={isCopied ? CheckCheck : undefined}
                              tone={isCopied ? 'success' : undefined}
                              onClick={() => void copyText(item)}
                            />
                          )}

                          {item.text && (
                            <BorderedRowActionIconButton
                              data-help-id="transcribe-download"
                              label={m.app_transcribe_download_label()}
                              action="download"
                              onClick={() => downloadText(item)}
                            />
                          )}

                          <BorderedRowActionIconButton
                            label={m.app_transcribe_delete_label()}
                            action="delete"
                            onClick={() => {
                              cancelEdit()
                              setConfirmingDeleteId(item.id)
                            }}
                          />
                        </RowActionGroup>
                      </InlineDeleteConfirm>
                    </ListRowActions>
                  </>
                )}
              </ListRow>
            )
          })}
        </ListFrame>
      )}
    </div>
  )
}
