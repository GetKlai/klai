import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Tooltip } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
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
} from 'lucide-react'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/transcribe/')({
  component: () => (
    <ProductGuard product="scribe">
      <TranscribePage />
    </ProductGuard>
  ),
})

const SCRIBE_BASE = '/scribe/v1'
const BOTS_BASE = '/api/bots'
const ACTIVE_MEETING_STATUSES = ['pending', 'joining', 'recording', 'processing']

interface TranscriptionItem {
  id: string
  name: string | null
  text: string
  language: string
  duration_seconds: number
  created_at: string
  has_summary?: boolean
}

interface TranscriptionListResponse {
  items: TranscriptionItem[]
  total: number
}

interface MeetingListItem {
  id: string
  platform: string
  meeting_url: string
  meeting_title: string | null
  status: string
  created_at: string
  duration_seconds: number | null
  transcript_text: string | null
  language: string | null
}

interface MeetingListResponse {
  items: MeetingListItem[]
  total: number
}

type Source = 'upload' | 'meeting'

interface UnifiedItem {
  id: string
  source: Source
  title: string | null
  text: string | null
  language: string | null
  duration_seconds: number | null
  created_at: string
  status: string
  uploadName?: string | null
  meeting_url?: string
  platform?: string
  has_summary?: boolean
}

function toUnified(item: TranscriptionItem): UnifiedItem {
  return {
    id: item.id,
    source: 'upload',
    title: item.name,
    text: item.text,
    language: item.language,
    duration_seconds: item.duration_seconds,
    created_at: item.created_at,
    status: 'done',
    uploadName: item.name,
    has_summary: item.has_summary,
  }
}

function meetingToUnified(item: MeetingListItem): UnifiedItem {
  return {
    id: item.id,
    source: 'meeting',
    title: item.meeting_title ?? item.meeting_url,
    text: item.transcript_text,
    language: item.language,
    duration_seconds: item.duration_seconds,
    created_at: item.created_at,
    status: item.status,
    meeting_url: item.meeting_url,
    platform: item.platform,
  }
}

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

function MeetingStatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; classes: string }> = {
    pending:    { label: m.app_meetings_status_pending(),    classes: 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-accent)]' },
    joining:    { label: m.app_meetings_status_joining(),    classes: 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-accent)] animate-pulse' },
    recording:  { label: m.app_meetings_status_recording(),  classes: 'bg-[var(--color-destructive)]/10 text-[var(--color-destructive)] animate-pulse' },
    processing: { label: m.app_meetings_status_processing(), classes: 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-accent)] animate-pulse' },
    done:       { label: m.app_meetings_status_done(),       classes: 'bg-[var(--color-success)]/10 text-[var(--color-success)]' },
    failed:     { label: m.app_meetings_status_failed(),     classes: 'bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]' },
  }
  const c = config[status] ?? { label: status, classes: 'bg-[var(--color-sand-mid)] text-[var(--color-purple-deep)]' }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${c.classes}`}>
      {c.label}
    </span>
  )
}

function TranscribePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  function handleNavigateToDetail(item: UnifiedItem) {
    if (item.source === 'upload') {
      void navigate({ to: '/app/transcribe/$transcriptionId', params: { transcriptionId: item.id } })
    } else {
      void navigate({ to: '/app/meetings/$meetingId', params: { meetingId: String(item.id) } })
    }
  }

  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState<string>('')
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  const { data: transcriptionsData, isLoading: transcriptionsLoading } = useQuery<TranscriptionListResponse>({
    queryKey: ['transcriptions', token],
    queryFn: async () => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions?limit=50`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      return res.json()
    },
    enabled: !!token,
  })

  const { data: meetingsData, isLoading: meetingsLoading } = useQuery<MeetingListResponse>({
    queryKey: ['meetings', token],
    queryFn: async () => {
      const res = await fetch(`${BOTS_BASE}/meetings?limit=50`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      return res.json()
    },
    enabled: !!token,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return false
      return data.items.some((mtg) => ACTIVE_MEETING_STATUSES.includes(mtg.status)) ? 5000 : false
    },
  })

  const allItems: UnifiedItem[] = [
    ...(transcriptionsData?.items ?? []).map(toUnified),
    ...(meetingsData?.items ?? []).map(meetingToUnified),
  ].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  const isLoading = transcriptionsLoading || meetingsLoading
  const hasActiveMeetings = (meetingsData?.items ?? []).some((mtg) =>
    ACTIVE_MEETING_STATUSES.includes(mtg.status),
  )

  const deleteUploadMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['transcriptions', token] }),
  })

  const deleteMeetingMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${BOTS_BASE}/meetings/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['meetings', token] }),
  })

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string | null }) => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions/${id}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!res.ok) throw new Error('Opslaan mislukt')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['transcriptions', token] })
      setEditingId(null)
    },
  })

  const stopMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${BOTS_BASE}/meetings/${id}/stop`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Stoppen mislukt')
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['meetings', token] }),
  })

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
    renameMutation.mutate({ id, name: editName.trim() || null })
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
      deleteUploadMutation.mutate(item.id)
    } else {
      deleteMeetingMutation.mutate(item.id)
    }
  }

  const filteredItems = search.trim()
    ? allItems.filter((item) => {
        const q = search.toLowerCase()
        return (
          item.text?.toLowerCase().includes(q) ||
          item.title?.toLowerCase().includes(q) ||
          item.meeting_url?.toLowerCase().includes(q)
        )
      })
    : allItems

  const totalCount = (transcriptionsData?.total ?? 0) + (meetingsData?.total ?? 0)

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.app_tool_transcribe_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && m.app_transcribe_count_total({ count: String(totalCount) })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => void navigate({ to: '/app/meetings/start' })}
          >
            <Video className="mr-2 h-4 w-4" />
            {m.app_transcribe_new_meeting()}
          </Button>
          <Button
            data-help-id="transcribe-add"
            onClick={() => void navigate({ to: '/app/transcribe/add' })}
          >
            <Mic className="mr-2 h-4 w-4" />
            {m.app_transcribe_new_audio()}
          </Button>
        </div>
      </div>

      {hasActiveMeetings && (
        <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>{m.app_transcribe_auto_refresh()}</span>
        </div>
      )}

      <Card data-help-id="transcribe-list">
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : allItems.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <Mic className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
              <div className="space-y-1">
                <p className="font-medium text-[var(--color-purple-deep)]">
                  {m.app_transcribe_empty_heading()}
                </p>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {m.app_transcribe_empty_body()}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void navigate({ to: '/app/transcribe/add' })}
                className="mt-2"
              >
                <Mic className="mr-2 h-3.5 w-3.5" />
                {m.app_transcribe_new_audio()}
              </Button>
            </div>
          ) : (
            <>
              <div className="px-4 pt-3 pb-2 border-b border-[var(--color-border)]">
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={m.app_transcribe_search_placeholder()}
                  className="h-8 text-sm max-w-xs"
                />
              </div>
              {filteredItems.length === 0 ? (
                <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
                  {m.app_transcribe_search_empty()}
                </p>
              ) : (
                <table className="w-full text-sm table-fixed">
                  <thead>
                    <tr className="border-b border-[var(--color-border)]">
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-1/2">
                        {m.app_transcribe_col_text()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_words()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_language()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_duration()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_date()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_source()}
                      </th>
                      <th className="px-6 py-3 w-36" />
                    </tr>
                  </thead>
                  <tbody>
                    {filteredItems.map((item, i) => {
                      const isEditing = editingId === item.id
                      const isConfirmingDelete = confirmingDeleteId === item.id
                      const isSaving = renameMutation.isPending && renameMutation.variables?.id === item.id
                      const isDeleting =
                        (deleteUploadMutation.isPending && deleteUploadMutation.variables === item.id) ||
                        (deleteMeetingMutation.isPending && deleteMeetingMutation.variables === item.id)
                      const isCopied = copiedId === item.id
                      const isActive = item.source === 'meeting' && ACTIVE_MEETING_STATUSES.includes(item.status)
                      const isStopping = stopMutation.isPending && stopMutation.variables === item.id

                      return (
                        <tr
                          key={`${item.source}-${item.id}`}
                          className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                        >
                          {/* Title / preview */}
                          <td className="px-6 py-3 text-[var(--color-purple-deep)] max-w-xs">
                            {isEditing ? (
                              <Input
                                value={editName}
                                onChange={(e) => setEditName(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') saveEdit(item.id)
                                  if (e.key === 'Escape') cancelEdit()
                                }}
                                disabled={isSaving}
                                autoFocus
                                className="h-7 text-sm"
                              />
                            ) : (
                              <div className="flex items-start gap-1.5">
                                <Tooltip
                                  label={
                                    item.source === 'upload'
                                      ? m.app_transcribe_source_audio()
                                      : m.app_transcribe_source_meeting()
                                  }
                                >
                                  {item.source === 'upload' ? (
                                    <Mic className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)]" />
                                  ) : (
                                    <Video className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)]" />
                                  )}
                                </Tooltip>
                                <div className="min-w-0">
                                  {item.title ? (
                                    <div>
                                      <div className="flex items-center gap-1.5">
                                        {(item.source === 'meeting' && item.status === 'done') || item.source === 'upload' ? (
                                          <button
                                            type="button"
                                            className="block truncate font-medium text-left hover:underline cursor-pointer"
                                            onClick={() => handleNavigateToDetail(item)}
                                          >
                                            {item.title}
                                          </button>
                                        ) : (
                                          <span className="block truncate font-medium">{item.title}</span>
                                        )}
                                        {item.source === 'upload' && item.has_summary && (
                                          <Tooltip label={m.app_transcribe_has_summary()}>
                                            <FileText className="h-3 w-3 shrink-0 text-[var(--color-purple-deep)]" />
                                          </Tooltip>
                                        )}
                                      </div>
                                      {item.text && (
                                        <span className="block truncate text-xs text-[var(--color-muted-foreground)]">
                                          {item.text}
                                        </span>
                                      )}
                                    </div>
                                  ) : (
                                    <span className="block truncate">{item.text ?? item.meeting_url ?? '—'}</span>
                                  )}
                                  {item.source === 'meeting' && item.status !== 'done' && (
                                    <div className="mt-1">
                                      <MeetingStatusBadge status={item.status} />
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}
                          </td>

                          {/* Words */}
                          <td className="px-6 py-3 text-[var(--color-muted-foreground)] tabular-nums">
                            {item.text
                              ? item.text.trim().split(/\s+/).filter(Boolean).length.toLocaleString()
                              : '—'}
                          </td>

                          {/* Language */}
                          <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                            {item.language ? (
                              <div className="flex items-center gap-1.5">
                                <img
                                  src={`https://flagcdn.com/16x12/${languageToCountryCode(item.language)}.png`}
                                  width="16"
                                  height="12"
                                  alt={item.language.toUpperCase()}
                                  className="rounded-sm shrink-0"
                                />
                                <span>{item.language.toUpperCase()}</span>
                              </div>
                            ) : (
                              <span className="text-[var(--color-muted-foreground)]">—</span>
                            )}
                          </td>

                          {/* Duration */}
                          <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                            {item.duration_seconds != null
                              ? formatDuration(item.duration_seconds)
                              : <span className="text-[var(--color-muted-foreground)]">—</span>}
                          </td>

                          {/* Date */}
                          <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                            {formatDate(item.created_at)}
                          </td>

                          {/* Source */}
                          <td className="px-6 py-3 text-[var(--color-muted-foreground)] text-xs">
                            {item.source === 'upload'
                              ? m.app_transcribe_source_audio()
                              : m.app_transcribe_source_meeting()}
                          </td>

                          {/* Actions */}
                          <td className="px-6 py-3 w-36 text-right">
                            {isEditing ? (
                              <div className="flex items-center justify-end gap-1">
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
                            ) : isConfirmingDelete ? (
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

                                {/* Active meeting: stop button */}
                                {isActive && (
                                  <Tooltip label={m.app_meetings_stop_button()}>
                                    <button
                                      onClick={() => stopMutation.mutate(item.id)}
                                      disabled={isStopping}
                                      aria-label={m.app_meetings_stop_button()}
                                      className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                                    >
                                      {isStopping ? (
                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                      ) : (
                                        <Square className="h-3.5 w-3.5" />
                                      )}
                                    </button>
                                  </Tooltip>
                                )}

                                {/* Copy transcript */}
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

                                {/* Download transcript */}
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
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
