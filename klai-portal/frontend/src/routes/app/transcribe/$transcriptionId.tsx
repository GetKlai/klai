import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { ArrowLeft, Loader2, Copy, CheckCheck, Download } from 'lucide-react'
import Markdown from 'react-markdown'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/transcribe/$transcriptionId')({
  component: TranscriptionDetailPage,
})

const SCRIBE_BASE = '/api/scribe/v1'

interface TranscriptionDetail {
  id: string
  name: string | null
  text: string
  language: string
  duration_seconds: number
  created_at: string
  summary_json: {
    type: 'meeting' | 'recording'
    markdown: string
    structured: Record<string, unknown>
  } | null
}

/** Strip markdown syntax to produce plain text for clipboard copy */
function stripMarkdown(md: string): string {
  return md
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/^\s*[-*]\s+/gm, '- ')
    .trim()
}

function TranscriptionDetailPage() {
  const { transcriptionId } = Route.useParams()
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [copied, setCopied] = useState(false)
  const [summaryCopied, setSummaryCopied] = useState<'text' | 'markdown' | null>(null)
  const [recordingType, setRecordingType] = useState<'meeting' | 'recording'>('recording')

  const { data: transcription, isLoading } = useQuery<TranscriptionDetail>({
    queryKey: ['transcription', transcriptionId],
    queryFn: async () => apiFetch<TranscriptionDetail>(`${SCRIBE_BASE}/transcriptions/${transcriptionId}`),
    enabled: auth.isAuthenticated,
  })

  const summarizeMutation = useMutation({
    mutationFn: async (force: boolean) => {
      const url = `${SCRIBE_BASE}/transcriptions/${transcriptionId}/summarize${force ? '?force=true' : ''}`
      return apiFetch(url, {
        method: 'POST',
        body: JSON.stringify({
          recording_type: recordingType,
          language: transcription?.language ?? null,
        }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['transcription', transcriptionId] })
    },
  })

  async function copyTranscript() {
    if (!transcription?.text) return
    await navigator.clipboard.writeText(transcription.text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  function downloadTranscript() {
    if (!transcription?.text) return
    const blob = new Blob([transcription.text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${transcription.name ?? 'transcriptie'}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function copySummaryText() {
    if (!transcription?.summary_json?.markdown) return
    await navigator.clipboard.writeText(stripMarkdown(transcription.summary_json.markdown))
    setSummaryCopied('text')
    setTimeout(() => setSummaryCopied(null), 2000)
  }

  async function copySummaryMarkdown() {
    if (!transcription?.summary_json?.markdown) return
    await navigator.clipboard.writeText(transcription.summary_json.markdown)
    setSummaryCopied('markdown')
    setTimeout(() => setSummaryCopied(null), 2000)
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" />
      </div>
    )
  }

  if (!transcription) return null

  const displayTitle = transcription.name ?? (
    transcription.text.length > 60
      ? transcription.text.slice(0, 60) + '...'
      : transcription.text
  )

  return (
    <div className="p-6 max-w-3xl">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {displayTitle}
        </h1>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/app/transcribe' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_transcribe_detail_back()}
        </Button>
      </div>

      <div className="space-y-6">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="text-base font-medium">
              {m.app_transcribe_detail_transcript_title()}
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
            <p className="text-sm text-[var(--color-muted-foreground)] whitespace-pre-wrap">
              {transcription.text}
            </p>
          </CardContent>
        </Card>

        {transcription.text.trim() && (
          <div className="flex items-center gap-3">
            <label className="text-sm font-medium text-[var(--color-foreground)] shrink-0">
              {m.app_transcribe_summary_type_label()}
            </label>
            <Select
              value={recordingType}
              onChange={(e) => setRecordingType(e.target.value as 'meeting' | 'recording')}
              className="w-48"
            >
              <option value="recording">{m.app_transcribe_summary_type_recording()}</option>
              <option value="meeting">{m.app_transcribe_summary_type_meeting()}</option>
            </Select>
            <Button
              variant="outline"
              size="sm"
              onClick={() => summarizeMutation.mutate(!!transcription.summary_json)}
              disabled={summarizeMutation.isPending}
            >
              {summarizeMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {m.app_transcribe_summary_loading()}
                </>
              ) : transcription.summary_json ? (
                m.app_transcribe_resummarize_button()
              ) : (
                m.app_transcribe_summarize_button()
              )}
            </Button>
          </div>
        )}

        {summarizeMutation.error && (
          <Card className="border-[var(--color-destructive)]">
            <CardContent className="pt-4">
              <p className="text-sm font-medium text-[var(--color-destructive)]">
                {m.app_transcribe_summary_error()}
              </p>
              <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">{summarizeMutation.error.message}</p>
            </CardContent>
          </Card>
        )}

        {transcription.summary_json && (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <CardTitle className="text-base font-medium">
                {m.app_transcribe_detail_summary_title()}
              </CardTitle>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={copySummaryText}>
                  {summaryCopied === 'text' ? (
                    <>
                      <CheckCheck className="mr-1.5 h-3.5 w-3.5 text-[var(--color-success)]" />
                      {m.app_transcribe_summary_copy_done()}
                    </>
                  ) : (
                    <>
                      <Copy className="mr-1.5 h-3.5 w-3.5" />
                      {m.app_transcribe_summary_copy_text()}
                    </>
                  )}
                </Button>
                <Button variant="outline" size="sm" onClick={copySummaryMarkdown}>
                  {summaryCopied === 'markdown' ? (
                    <>
                      <CheckCheck className="mr-1.5 h-3.5 w-3.5 text-[var(--color-success)]" />
                      {m.app_transcribe_summary_copy_done()}
                    </>
                  ) : (
                    <>
                      <Copy className="mr-1.5 h-3.5 w-3.5" />
                      {m.app_transcribe_summary_copy_markdown()}
                    </>
                  )}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="text-sm text-[var(--color-foreground)] space-y-1 [&_h1]:font-semibold [&_h1]:mt-3 [&_h2]:font-semibold [&_h2]:mt-3 [&_h3]:font-semibold [&_h3]:mt-2 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:list-decimal [&_ol]:pl-4 [&_li]:mt-0.5 [&_strong]:font-semibold [&_p]:leading-relaxed">
                <Markdown>{transcription.summary_json.markdown}</Markdown>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
