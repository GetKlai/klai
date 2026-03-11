import { createFileRoute } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Upload, Copy, Trash2, CheckCheck, Loader2, Mic } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/transcribe')({
  component: TranscribePage,
})

const SCRIBE_BASE = '/scribe/v1'
const ACCEPTED_TYPES = '.wav,.mp3,.m4a,.ogg,.webm'
const MAX_MB = 100

interface TranscriptionItem {
  id: string
  text: string
  language: string
  duration_seconds: number
  inference_time_seconds: number | null
  created_at: string
}

interface TranscriptionListResponse {
  items: TranscriptionItem[]
  total: number
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function TranscribePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [language, setLanguage] = useState<string>('')
  const [result, setResult] = useState<TranscriptionItem | null>(null)
  const [copied, setCopied] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const { data: history, isLoading: historyLoading } = useQuery<TranscriptionListResponse>({
    queryKey: ['transcriptions', token],
    queryFn: async () => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions?limit=20`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      return res.json()
    },
    enabled: !!token,
  })

  const transcribeMutation = useMutation({
    mutationFn: async (file: File) => {
      setUploadError(null)
      const form = new FormData()
      form.append('file', file)
      if (language) form.append('language', language)
      const res = await fetch(`${SCRIBE_BASE}/transcribe`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body?.detail ?? m.app_transcribe_error_generic())
      }
      return res.json() as Promise<TranscriptionItem>
    },
    onSuccess: (data) => {
      setResult(data)
      setSelectedFile(null)
      queryClient.invalidateQueries({ queryKey: ['transcriptions', token] })
    },
    onError: (err: Error) => {
      setUploadError(err.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: (_data, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['transcriptions', token] })
      if (result?.id === deletedId) setResult(null)
    },
  })

  const handleFile = (file: File) => {
    setUploadError(null)
    setResult(null)
    if (file.size > MAX_MB * 1024 * 1024) {
      setUploadError(m.app_transcribe_error_too_large({ max: MAX_MB }))
      return
    }
    setSelectedFile(file)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  const handleCopy = () => {
    if (!result) return
    navigator.clipboard.writeText(result.text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const isTranscribing = transcribeMutation.isPending

  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.app_tool_transcribe_title()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.app_transcribe_subtitle()}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{m.app_transcribe_card_title()}</CardTitle>
          <CardDescription>{m.app_transcribe_upload_description()}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
              dragging
                ? 'border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5'
                : 'border-[var(--color-border)] hover:border-[var(--color-purple-accent)]/50'
            }`}
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_TYPES}
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
            />
            <Upload className="mx-auto mb-3 h-8 w-8 text-[var(--color-muted-foreground)]" />
            {selectedFile ? (
              <div>
                <p className="font-medium text-sm">{selectedFile.name}</p>
                <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
                  {(selectedFile.size / 1024 / 1024).toFixed(1)} MB
                </p>
              </div>
            ) : (
              <div>
                <p className="text-sm font-medium">{m.app_transcribe_dropzone_label()}</p>
                <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
                  {m.app_transcribe_dropzone_hint({ formats: 'WAV, MP3, M4A, OGG, WebM', max: MAX_MB })}
                </p>
              </div>
            )}
          </div>

          <div className="flex items-center gap-3">
            <label className="text-sm font-medium shrink-0">{m.app_transcribe_language_label()}</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="text-sm border border-[var(--color-border)] rounded-md px-3 py-1.5 bg-transparent"
            >
              <option value="">{m.app_transcribe_language_auto()}</option>
              <option value="nl">Nederlands</option>
              <option value="en">English</option>
              <option value="de">Deutsch</option>
              <option value="fr">Français</option>
            </select>
          </div>

          {uploadError && (
            <p className="text-sm text-red-600">{uploadError}</p>
          )}

          <Button
            onClick={() => selectedFile && transcribeMutation.mutate(selectedFile)}
            disabled={!selectedFile || isTranscribing}
            className="w-full"
          >
            {isTranscribing ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {m.app_transcribe_processing()}
              </>
            ) : (
              <>
                <Mic className="mr-2 h-4 w-4" />
                {m.app_transcribe_submit()}
              </>
            )}
          </Button>

          {isTranscribing && (
            <p className="text-xs text-[var(--color-muted-foreground)] text-center">
              {m.app_transcribe_processing_hint()}
            </p>
          )}
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle>{m.app_transcribe_result_title()}</CardTitle>
                <CardDescription>
                  {m.app_transcribe_result_meta({
                    language: result.language.toUpperCase(),
                    duration: formatDuration(result.duration_seconds),
                  })}
                </CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={handleCopy}>
                {copied ? <CheckCheck className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm whitespace-pre-wrap leading-relaxed">{result.text}</p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{m.app_transcribe_history_title()}</CardTitle>
        </CardHeader>
        <CardContent>
          {historyLoading ? (
            <div className="flex justify-center py-4">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : !history?.items?.length ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.app_transcribe_history_empty()}</p>
          ) : (
            <div className="space-y-3">
              {history.items.map((item) => (
                <div
                  key={item.id}
                  className="flex items-start gap-3 p-3 rounded-lg border border-[var(--color-border)] hover:bg-[var(--color-muted)]/30 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm truncate">{item.text}</p>
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                      {item.language.toUpperCase()} · {formatDuration(item.duration_seconds)} ·{' '}
                      {new Date(item.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <button
                    onClick={() => deleteMutation.mutate(item.id)}
                    disabled={deleteMutation.isPending && deleteMutation.variables === item.id}
                    className="shrink-0 p-1 text-[var(--color-muted-foreground)] hover:text-red-500 transition-colors disabled:opacity-50"
                    aria-label={m.app_transcribe_delete_label()}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
