import { createFileRoute, useNavigate, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { Plus, Loader2, Square, Trash2, Check, X, Video } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/meetings/')({
  component: MeetingsPage,
})

const BOTS_BASE = '/api/bots'
const ACTIVE_STATUSES = ['pending', 'joining', 'recording', 'processing']

interface TranscriptSegment {
  start: number
  end: number
  text: string
  speaker: string
}

interface MeetingItem {
  id: string
  platform: string
  meeting_url: string
  meeting_title: string | null
  status: string
  consent_given: boolean
  transcript_text: string | null
  transcript_segments: TranscriptSegment[] | null
  language: string | null
  duration_seconds: number | null
  error_message: string | null
  started_at: string | null
  ended_at: string | null
  created_at: string
}

interface MeetingsListResponse {
  items: MeetingItem[]
  total: number
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '\u2014'
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('nl-NL', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function platformLabel(platform: string): string {
  const map: Record<string, string> = {
    google_meet: 'Google Meet',
    zoom: 'Zoom',
    teams: 'Microsoft Teams',
  }
  return map[platform] ?? platform
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; classes: string }> = {
    pending: {
      label: m.app_meetings_status_pending(),
      classes: 'bg-blue-100 text-blue-800',
    },
    joining: {
      label: m.app_meetings_status_joining(),
      classes: 'bg-blue-100 text-blue-800 animate-pulse',
    },
    recording: {
      label: m.app_meetings_status_recording(),
      classes: 'bg-red-100 text-red-800 animate-pulse',
    },
    processing: {
      label: m.app_meetings_status_processing(),
      classes: 'bg-amber-100 text-amber-800 animate-pulse',
    },
    done: {
      label: m.app_meetings_status_done(),
      classes: 'bg-green-100 text-green-800',
    },
    failed: {
      label: m.app_meetings_status_failed(),
      classes: 'bg-red-100 text-red-800',
    },
  }
  const c = config[status] ?? { label: status, classes: 'bg-gray-100 text-gray-800' }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${c.classes}`}
    >
      {c.label}
    </span>
  )
}

function MeetingsPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)

  const { data, isLoading } = useQuery<MeetingsListResponse>({
    queryKey: ['meetings', token],
    queryFn: async () => {
      const res = await fetch(`${BOTS_BASE}/meetings?limit=50`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      return res.json()
    },
    enabled: !!token,
    refetchInterval: (query) =>
      query.state.data?.items?.some((i: MeetingItem) => ACTIVE_STATUSES.includes(i.status))
        ? 5000
        : false,
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

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${BOTS_BASE}/meetings/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      setConfirmingDeleteId(null)
      void queryClient.invalidateQueries({ queryKey: ['meetings', token] })
    },
  })

  const items = data?.items ?? []
  const countLabel =
    data?.total === 1
      ? m.app_meetings_count_one()
      : m.app_meetings_count({ count: String(data?.total ?? 0) })

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.app_tool_meetings_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{!isLoading && countLabel}</p>
        </div>
        <Button onClick={() => navigate({ to: '/app/meetings/start' })}>
          <Plus className="mr-2 h-4 w-4" />
          {m.app_meetings_start_button()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <Video className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
              <div className="space-y-1">
                <p className="font-medium text-[var(--color-purple-deep)]">
                  {m.app_meetings_empty_heading()}
                </p>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {m.app_meetings_empty_body()}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate({ to: '/app/meetings/start' })}
                className="mt-2"
              >
                <Plus className="mr-2 h-3.5 w-3.5" />
                {m.app_meetings_start_button()}
              </Button>
            </div>
          ) : (
            <table className="w-full text-sm table-fixed">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-2/5">
                    {m.app_meetings_col_title()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_meetings_col_platform()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_meetings_col_status()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_meetings_col_duration()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_meetings_col_date()}
                  </th>
                  <th className="px-6 py-3 w-24" />
                </tr>
              </thead>
              <tbody>
                {items.map((item, i) => {
                  const isConfirmingDelete = confirmingDeleteId === item.id
                  const isDeleting =
                    deleteMutation.isPending && deleteMutation.variables === item.id
                  const isStopping =
                    stopMutation.isPending && stopMutation.variables === item.id
                  const canStop = ['recording', 'joining'].includes(item.status)

                  return (
                    <tr
                      key={item.id}
                      className={
                        i % 2 === 0
                          ? 'bg-[var(--color-card)]'
                          : 'bg-[var(--color-secondary)]'
                      }
                    >
                      <td className="px-6 py-3 text-[var(--color-purple-deep)] max-w-xs">
                        <Link
                          to="/app/meetings/$meetingId"
                          params={{ meetingId: item.id }}
                          className="hover:underline truncate block"
                        >
                          {item.meeting_title ?? item.meeting_url}
                        </Link>
                      </td>
                      <td className="px-6 py-3 text-[var(--color-muted-foreground)]">
                        {platformLabel(item.platform)}
                      </td>
                      <td className="px-6 py-3">
                        <StatusBadge status={item.status} />
                      </td>
                      <td className="px-6 py-3 text-[var(--color-purple-deep)] tabular-nums">
                        {formatDuration(item.duration_seconds)}
                      </td>
                      <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                        {formatDate(item.created_at)}
                      </td>
                      <td className="px-6 py-3 w-24 text-right">
                        {isConfirmingDelete ? (
                          <div className="flex items-center justify-end gap-1">
                            {isDeleting ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <>
                                <button
                                  onClick={() => deleteMutation.mutate(item.id)}
                                  aria-label={m.app_meetings_delete_confirm()}
                                  className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-destructive)] text-white hover:opacity-90"
                                >
                                  <Check className="h-3.5 w-3.5" />
                                </button>
                                <button
                                  onClick={() => setConfirmingDeleteId(null)}
                                  aria-label={m.app_meetings_delete_cancel()}
                                  className="flex h-7 w-7 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] hover:bg-[var(--color-border)]"
                                >
                                  <X className="h-3.5 w-3.5" />
                                </button>
                              </>
                            )}
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-1">
                            {canStop && (
                              <Tooltip label={m.app_meetings_stop_button()}>
                                <button
                                  onClick={() => stopMutation.mutate(item.id)}
                                  disabled={isStopping}
                                  aria-label={m.app_meetings_stop_button()}
                                  className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70 disabled:opacity-40"
                                >
                                  {isStopping ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  ) : (
                                    <Square className="h-3.5 w-3.5" />
                                  )}
                                </button>
                              </Tooltip>
                            )}
                            <Tooltip label={m.app_meetings_delete_button()}>
                              <button
                                onClick={() => setConfirmingDeleteId(item.id)}
                                aria-label={m.app_meetings_delete_button()}
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
        </CardContent>
      </Card>
    </div>
  )
}
