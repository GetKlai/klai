import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { ArrowLeft, Loader2, Square, Copy, CheckCheck, Download, FileJson } from 'lucide-react'
import Markdown from 'react-markdown'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'

type TabId = 'summary' | 'transcript'

const VALID_TABS = new Set<TabId>(['summary', 'transcript'])

type MeetingSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/app/meetings/$meetingId')({
  validateSearch: (search: Record<string, unknown>): MeetingSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: () => (
    <ProductGuard product="scribe">
      <MeetingDetailPage />
    </ProductGuard>
  ),
})

const BOTS_BASE = '/api/bots'
const ACTIVE_STATUSES = ['pending', 'joining', 'recording', 'stopping', 'processing']

interface TranscriptSegment {
  start: number
  end: number
  text: string
  speaker: string
}

interface SummaryStructured {
  speakers: string[]
  topics: string[]
  decisions: (string | { decision: string; rationale: string | null; decided_by: string | null })[]
  action_items: { owner: string | null; task: string; deadline?: string | null }[]
  key_quotes?: string[]
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
    stopping:   { label: m.app_meetings_status_stopping(),   variant: 'warning',     pulse: true },
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

/** Build a markdown action items section from structured data */
function buildActionItemsMd(
  items: { owner: string | null; task: string; deadline?: string | null }[],
  title: string,
): string {
  if (!items.length) return ''
  const lines = items.map((item) => {
    let line = item.owner ? `**${item.owner}**: ${item.task}` : item.task
    if (item.deadline) line += ` _(${item.deadline})_`
    return `- ${line}`
  })
  return `\n\n## ${title}\n\n${lines.join('\n')}`
}

/** Build a markdown key quotes section */
function buildKeyQuotesMd(quotes: string[], title: string): string {
  if (!quotes.length) return ''
  const lines = quotes.map((q) => `> ${q}`)
  return `\n\n## ${title}\n\n${lines.join('\n\n')}`
}

/** Strip markdown syntax to produce plain text for clipboard copy */
function stripMarkdown(md: string): string {
  return md
    .replace(/^#{1,6}\s+/gm, '')          // headings
    .replace(/\*\*(.+?)\*\*/g, '$1')      // bold
    .replace(/\*(.+?)\*/g, '$1')          // italic
    .replace(/`(.+?)`/g, '$1')            // inline code
    .replace(/^\s*[-*]\s+/gm, '- ')       // normalize bullets
    .trim()
}

function MeetingDetailPage() {
  const { meetingId } = Route.useParams()
  const search = Route.useSearch()
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)
  const [summaryCopied, setSummaryCopied] = useState<'text' | 'markdown' | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)

  const { data: meeting, isLoading } = useQuery<MeetingDetail>({
    queryKey: ['meeting', meetingId],
    queryFn: async () => apiFetch<MeetingDetail>(`${BOTS_BASE}/meetings/${meetingId}`),
    enabled: auth.isAuthenticated,
    refetchInterval: (query) =>
      query.state.data && ACTIVE_STATUSES.includes(query.state.data.status) ? 3000 : false,
  })

  const stopMutation = useMutation({
    mutationFn: async () => {
      await apiFetch(`${BOTS_BASE}/meetings/${meetingId}/stop`, { method: 'POST' })
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['meeting', meetingId] }),
  })

  const summarizeMutation = useMutation({
    mutationFn: async (force: boolean) => {
      const url = `${BOTS_BASE}/meetings/${meetingId}/summarize${force ? '?force=true' : ''}`
      return apiFetch(url, { method: 'POST' })
    },
    onSuccess: () => {
      setSummaryError(null)
      void queryClient.invalidateQueries({ queryKey: ['meeting', meetingId] })
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

  const fullSummaryMd = meeting?.summary_json
    ? meeting.summary_json.markdown +
      buildActionItemsMd(
        meeting.summary_json.structured?.action_items ?? [],
        m.app_meetings_action_items_title(),
      ) +
      buildKeyQuotesMd(
        meeting.summary_json.structured?.key_quotes ?? [],
        m.app_meetings_key_quotes_title(),
      )
    : ''

  async function copySummaryText() {
    if (!fullSummaryMd) return
    await navigator.clipboard.writeText(stripMarkdown(fullSummaryMd))
    setSummaryCopied('text')
    setTimeout(() => setSummaryCopied(null), 2000)
  }

  function downloadRaw() {
    if (!meeting?.transcript_segments) return
    const blob = new Blob([JSON.stringify(meeting.transcript_segments, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${meeting.meeting_title ?? 'vergadering'}-segments.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function copySummaryMarkdown() {
    if (!fullSummaryMd) return
    await navigator.clipboard.writeText(fullSummaryMd)
    setSummaryCopied('markdown')
    setTimeout(() => setSummaryCopied(null), 2000)
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!meeting) return null

  const canStop = ['recording', 'joining'].includes(meeting.status)
  const hasTranscript = meeting.status === 'done' && !!(meeting.transcript_text || meeting.transcript_segments?.length)
  const hasSummary = !!meeting.summary_json
  const meetingTitle = meeting.meeting_title ?? meeting.meeting_url
  const activeTab: TabId = search.tab ?? 'summary'
  const tabs: TabItem<TabId>[] = [
    { id: 'summary', label: m.app_meetings_summary_title() },
    { id: 'transcript', label: m.app_meetings_transcript_title() },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/app/meetings/$meetingId',
      params: { meetingId },
      search: { tab },
    })
  }

  function renderSummarizeButton(variant: 'default' | 'outline') {
    return (
      <Button
        variant={variant}
        size="sm"
        onClick={() => summarizeMutation.mutate(hasSummary)}
        disabled={summarizeMutation.isPending}
      >
        {summarizeMutation.isPending ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            {m.app_meetings_summary_loading()}
          </>
        ) : hasSummary ? (
          m.app_meetings_resummarize_button()
        ) : (
          m.app_meetings_summarize_button()
        )}
      </Button>
    )
  }

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10">
      <div className="mb-6 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="page-title text-[26px] font-display-bold leading-tight text-gray-900">
              {meetingTitle}
            </h1>
            <StatusBadge status={meeting.status} />
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/app/transcribe' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_meetings_back()}
        </Button>
      </div>

      <div className="space-y-6">
        {ACTIVE_STATUSES.includes(meeting.status) ? (
          <Card>
            <CardContent className="pt-4">
              <p className="text-sm text-gray-400">
                {m.app_meetings_active_info()}
              </p>
            </CardContent>
            {canStop && (
              <CardFooter className="flex justify-end pt-0">
                <Button
                  variant="destructive"
                  size="sm"
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
              </CardFooter>
            )}
          </Card>
        ) : null}

      {meeting.status === 'failed' && meeting.error_message && (
        <Card className="border-[var(--color-destructive)]">
          <CardContent className="pt-4">
            <p className="text-sm font-medium text-[var(--color-destructive)]">
              {m.app_meetings_error_label()}
            </p>
            <p className="mt-1 text-sm text-gray-400">
              {meeting.error_message}
            </p>
          </CardContent>
        </Card>
      )}

      {hasTranscript && (
        <div className="space-y-6">
          <div className="flex flex-col gap-3 border-b border-gray-200 sm:flex-row sm:items-end sm:justify-between">
            <Tabs
              tabs={tabs}
              value={activeTab}
              onValueChange={setTab}
              className="-mb-px border-b-0"
            />
            {activeTab === 'summary' && meeting.summary_json && (
              <div className="flex flex-wrap gap-2 pb-3 sm:justify-end">
                <Button variant="outline" size="sm" onClick={copySummaryText}>
                  {summaryCopied === 'text' ? (
                    <>
                      <CheckCheck className="mr-1.5 h-3.5 w-3.5 text-[var(--color-success)]" />
                      {m.app_meetings_summary_copy_done()}
                    </>
                  ) : (
                    <>
                      <Copy className="mr-1.5 h-3.5 w-3.5" />
                      {m.app_meetings_summary_copy_text()}
                    </>
                  )}
                </Button>
                <Button variant="outline" size="sm" onClick={copySummaryMarkdown}>
                  {summaryCopied === 'markdown' ? (
                    <>
                      <CheckCheck className="mr-1.5 h-3.5 w-3.5 text-[var(--color-success)]" />
                      {m.app_meetings_summary_copy_done()}
                    </>
                  ) : (
                    <>
                      <Copy className="mr-1.5 h-3.5 w-3.5" />
                      {m.app_meetings_summary_copy_markdown()}
                    </>
                  )}
                </Button>
                {renderSummarizeButton('outline')}
              </div>
            )}
            {activeTab === 'transcript' && (
              <div className="flex flex-wrap gap-2 pb-3 sm:justify-end">
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
                {meeting.transcript_segments && meeting.transcript_segments.length > 0 && (
                  <Button variant="outline" size="sm" onClick={downloadRaw}>
                    <FileJson className="mr-1.5 h-3.5 w-3.5" />
                    {m.app_meetings_download_raw()}
                  </Button>
                )}
              </div>
            )}
          </div>

          {summaryError && activeTab === 'summary' && (
            <Card className="border-[var(--color-destructive)]">
              <CardContent className="pt-4">
                <p className="text-sm font-medium text-[var(--color-destructive)]">
                  {m.app_meetings_summary_error()}
                </p>
                <p className="mt-1 text-sm text-gray-400">{summaryError}</p>
              </CardContent>
            </Card>
          )}

          {activeTab === 'summary' && (
            <section aria-label={m.app_meetings_summary_title()}>
              {meeting.summary_json ? (
                <div className="text-sm text-gray-900 space-y-1 [&_h1]:font-semibold [&_h1]:mt-3 [&_h2]:font-semibold [&_h2]:mt-3 [&_h3]:font-semibold [&_h3]:mt-2 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:list-decimal [&_ol]:pl-4 [&_li]:mt-0.5 [&_strong]:font-semibold [&_p]:leading-relaxed">
                  <Markdown>{fullSummaryMd}</Markdown>
                </div>
              ) : (
                <div className="flex min-h-32 items-center justify-center">
                  {renderSummarizeButton('default')}
                </div>
              )}
            </section>
          )}

          {activeTab === 'transcript' && (
            <section aria-label={m.app_meetings_transcript_title()}>
              {meeting.transcript_segments && meeting.transcript_segments.length > 0 ? (
                <div className="space-y-2 text-sm">
                  {meeting.transcript_segments.map((seg, i) => (
                    <div key={i} className="flex gap-3">
                      <span className="shrink-0 text-xs text-gray-400 tabular-nums mt-0.5 w-14">
                        [{formatTimestamp(seg.start)}]
                      </span>
                      <div>
                        <span className="font-medium text-gray-900">
                          {seg.speaker}:{' '}
                        </span>
                        <span className="text-gray-900">{seg.text}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-400 whitespace-pre-wrap">
                  {meeting.transcript_text}
                </p>
              )}
            </section>
          )}
        </div>
      )}

      {meeting.status === 'done' && !hasTranscript && (
        <p className="text-sm text-gray-400">
          {m.app_meetings_transcript_empty()}
        </p>
      )}
      </div>
    </div>
  )
}
