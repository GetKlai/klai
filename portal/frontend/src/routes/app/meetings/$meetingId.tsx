import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
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

interface SummaryStructured {
  speakers: string[]
  topics: string[]
  decisions: string[]
  action_items: { owner: string | null; task: string }[]
  open_questions: string[]
  next_steps: string[]
}

interface SummaryJson {
  markdown: string
  structured: SummaryStructured
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
  summary_json: SummaryJson | null
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
  const config: Record<string, { label: string; variant: 'default' | 'secondary' | 'warning' | 'destructive' | 'success' | 'outline'; pulse?: boolean }> = {
    pending:    { label: m.app_meetings_status_pending(),    variant: 'secondary' },
    joining:    { label: m.app_meetings_status_joining(),    variant: 'default',     pulse: true },
    recording:  { label: m.app_meetings_status_recording(),  variant: 'destructive', pulse: true },
    processing: { label: m.app_meetings_status_processing(), variant: 'warning',     pulse: true },
    done:       { label: m.app_meetings_status_done(),       variant: 'success' },
    failed:     { label: m.app_meetings_status_failed(),     variant: 'destructive' },
  }
  const c = config[status] ?? { label: status, variant: 'outline' as const }
  return (
    <Badge variant={c.variant} className={c.pulse ? 'animate-pulse' : undefined}>
      {c.label}
    </Badge>
  )
}

function SummaryMarkdown({ markdown }: { markdown: string }) {
  return (
    <pre className="text-sm text-[var(--color-foreground)] whitespace-pre-wrap font-sans">
      {markdown}
    </pre>
  )
}

function MeetingDetailPage() {
  const { meetingId } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)
  const [summaryError, setSummaryError] = useState<string | null>(null)

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

  const summarizeMutation = useMutation({
    mutationFn: async (force: boolean) => {
      const url = `${BOTS_BASE}/meetings/${meetingId}/summarize${force ? '?force=true' : ''}`
      const res = await fetch(url, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
        throw new Error(err.detail ?? 'Summarization failed')
      }
      return res.json()
    },
    onSuccess: () => {
      setSummaryError(null)
      void queryClient.invalidateQueries({ queryKey: ['meeting', meetingId, token] })
    },
    onError: (err: Error) => {
      setSummaryError(err.message)
    },
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
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/app/transcribe' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_meetings_back()}
        </Button>
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

      <div className="space-y-2">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {meeting.meeting_title ?? meeting.meeting_url}
        </h1>
        <StatusBadge status={meeting.status} />
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
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.app_meetings_active_info()}
        </p>
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

      {hasTranscript && (
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => summarizeMutation.mutate(!!meeting.summary_json)}
            disabled={summarizeMutation.isPending}
          >
            {summarizeMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {m.app_meetings_summary_loading()}
              </>
            ) : meeting.summary_json ? (
              m.app_meetings_resummarize_button()
            ) : (
              m.app_meetings_summarize_button()
            )}
          </Button>
        </div>
      )}

      {summaryError && (
        <Card className="border-[var(--color-destructive)]">
          <CardContent className="pt-4">
            <p className="text-sm font-medium text-[var(--color-destructive)]">
              {m.app_meetings_summary_error()}
            </p>
            <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">{summaryError}</p>
          </CardContent>
        </Card>
      )}

      {meeting.summary_json && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-medium">
              {m.app_meetings_summary_title()}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="prose prose-sm max-w-none text-[var(--color-foreground)]">
              <SummaryMarkdown markdown={meeting.summary_json.markdown} />
            </div>
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
