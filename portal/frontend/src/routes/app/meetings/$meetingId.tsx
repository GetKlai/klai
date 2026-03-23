import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Loader2, Square, Copy, CheckCheck, Download } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/meetings/$meetingId')({
  component: MeetingDetailPage,
})

const BOTS_BASE = '/api/bots'
const ACTIVE_STATUSES = ['pending', 'joining', 'recording', 'processing']

interface TranscriptSegment {
  start: number
  end: number
  text: string
  speaker: string
}

interface MeetingDetail {
  id: string
  platform: string
  meeting_url: string
  meeting_title: string | null
  status: string
  transcript_text: string | null
  transcript_segments: TranscriptSegment[] | null
  language: string | null
  duration_seconds: number | null
  error_message: string | null
  started_at: string | null
  ended_at: string | null
  created_at: string
}

function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  if (h > 0)
    return `${h}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  return `${mins}:${secs.toString().padStart(2, '0')}`
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
      className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ${c.classes}`}
    >
      {c.label}
    </span>
  )
}

function MeetingDetailPage() {
  const { meetingId } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)

  const { data: meeting, isLoading } = useQuery<MeetingDetail>({
    queryKey: ['meeting', meetingId, token],
    queryFn: async () => {
      const res = await fetch(`${BOTS_BASE}/meetings/${meetingId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      return res.json()
    },
    enabled: !!token,
    refetchInterval: (query) =>
      query.state.data && ACTIVE_STATUSES.includes(query.state.data.status) ? 3000 : false,
  })

  const stopMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${BOTS_BASE}/meetings/${meetingId}/stop`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Stoppen mislukt')
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['meeting', meetingId, token] }),
  })

  async function copyTranscript() {
    if (!meeting?.transcript_text) return
    await navigator.clipboard.writeText(meeting.transcript_text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  function downloadTranscript() {
    if (!meeting?.transcript_text) return
    const blob = new Blob([meeting.transcript_text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${meeting.meeting_title ?? 'vergadering'}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function downloadAudio() {
    if (!meeting || !token) return
    const res = await fetch(`${BOTS_BASE}/meetings/${meetingId}/audio`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) return
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${meeting.meeting_title ?? 'opname'}.webm`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" />
      </div>
    )
  }

  if (!meeting) return null

  const canStop = ['recording', 'joining'].includes(meeting.status)
  const hasTranscript = meeting.status === 'done' && meeting.transcript_text

  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <button
        onClick={() => navigate({ to: '/app/meetings' })}
        className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {m.app_meetings_back()}
      </button>

      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {meeting.meeting_title ?? meeting.meeting_url}
          </h1>
          <StatusBadge status={meeting.status} />
        </div>
        {canStop && (
          <Button
            variant="destructive"
            onClick={() => stopMutation.mutate()}
            disabled={stopMutation.isPending}
          >
            {stopMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Square className="mr-2 h-4 w-4" />
            )}
            {m.app_meetings_stop_button()}
          </Button>
        )}
      </div>

      {meeting.status === 'failed' && meeting.error_message && (
        <Card className="border-[var(--color-destructive)]">
          <CardContent className="pt-4">
            <p className="text-sm font-medium text-[var(--color-destructive)]">
              {m.app_meetings_error_label()}
            </p>
            <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
              {meeting.error_message}
            </p>
          </CardContent>
        </Card>
      )}

      {ACTIVE_STATUSES.includes(meeting.status) && (
        <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>Automatisch vernieuwen...</span>
        </div>
      )}

      {hasTranscript && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="text-base font-medium">
              {m.app_meetings_transcript_title()}
            </CardTitle>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={copyTranscript}>
                {copied ? (
                  <>
                    <CheckCheck className="mr-1.5 h-3.5 w-3.5 text-[var(--color-success)]" />
                    {m.app_meetings_copy_done()}
                  </>
                ) : (
                  <>
                    <Copy className="mr-1.5 h-3.5 w-3.5" />
                    {m.app_meetings_copy()}
                  </>
                )}
              </Button>
              <Button variant="outline" size="sm" onClick={downloadTranscript}>
                <Download className="mr-1.5 h-3.5 w-3.5" />
                {m.app_meetings_download()}
              </Button>
              <Button variant="outline" size="sm" onClick={downloadAudio} title="Download ruwe audio (debug)">
                <Download className="mr-1.5 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                Audio
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {meeting.transcript_segments && meeting.transcript_segments.length > 0 ? (
              <div className="space-y-2 text-sm">
                {meeting.transcript_segments.map((seg, i) => (
                  <div key={i} className="flex gap-3">
                    <span className="shrink-0 text-xs text-[var(--color-muted-foreground)] tabular-nums mt-0.5 w-14">
                      [{formatTimestamp(seg.start)}]
                    </span>
                    <div>
                      <span className="font-medium text-[var(--color-purple-deep)]">
                        {seg.speaker}:{' '}
                      </span>
                      <span className="text-[var(--color-foreground)]">{seg.text}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-[var(--color-muted-foreground)] whitespace-pre-wrap">
                {meeting.transcript_text}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {meeting.status === 'done' && !hasTranscript && (
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.app_meetings_transcript_empty()}
        </p>
      )}
    </div>
  )
}
