import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useRef, useState, useEffect, useCallback } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { ArrowLeft, Upload, Copy, CheckCheck, Loader2, Mic, Square, RotateCcw } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'

const SCRIBE_BASE = '/scribe/v1'
const ACCEPTED_TYPES = '.wav,.mp3,.m4a,.ogg,.webm'
const MAX_MB = 100

type Tab = 'record' | 'upload'

const VALID_ADD_TABS = new Set<Tab>(['record', 'upload'])

type AddTranscribeSearch = { tab?: Tab }

export const Route = createFileRoute('/app/transcribe/add')({
  validateSearch: (search: Record<string, unknown>): AddTranscribeSearch => ({
    tab: (VALID_ADD_TABS as Set<string>).has(search.tab as string) ? (search.tab as Tab) : undefined,
  }),
  component: () => (
    <ProductGuard product="scribe">
      <AddTranscribePage />
    </ProductGuard>
  ),
})

interface TranscriptionResponse {
  id: string
  name: string | null
  status: string
  text: string | null
  language: string | null
  duration_seconds: number | null
  inference_time_seconds: number | null
  summary_json: Record<string, unknown> | null
  created_at: string
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function AddTranscribePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate({ from: '/app/transcribe/add' })

  const { tab: tabParam } = Route.useSearch()
  const activeTab: Tab = tabParam ?? 'record'
  const [language, setLanguage] = useState<string>('')
  const [result, setResult] = useState<TranscriptionResponse | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Upload tab
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)

  // Record tab
  const [micPermission, setMicPermission] = useState<'idle' | 'requesting' | 'granted' | 'denied'>('idle')
  const [micBlocked, setMicBlocked] = useState(false)
  const [recording, setRecording] = useState(false)
  const [recordDuration, setRecordDuration] = useState(0)
  const [audioLevel, setAudioLevel] = useState(0)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const animFrameRef = useRef<number | null>(null)
  const durationIntervalRef = useRef<number | null>(null)

  const transcribeMutation = useMutation({
    mutationFn: async (file: File) => {
      setError(null)
      const form = new FormData()
      form.append('file', file)
      if (language) form.append('language', language)
      return apiFetch<TranscriptionResponse>(`${SCRIBE_BASE}/transcribe`, token, {
        method: 'POST',
        body: form,
      })
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['transcriptions'] })
      if (data.status === 'transcribed') {
        void navigate({ to: '/app/transcribe/$transcriptionId', params: { transcriptionId: data.id } })
      } else {
        setResult(data)
      }
      setSelectedFile(null)
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const retryMutation = useMutation({
    mutationFn: async (txnId: string) => {
      setError(null)
      const params = language ? `?language=${encodeURIComponent(language)}` : ''
      return apiFetch<TranscriptionResponse>(`${SCRIBE_BASE}/transcriptions/${txnId}/retry${params}`, token, {
        method: 'POST',
      })
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['transcriptions'] })
      if (data.status === 'transcribed') {
        void navigate({ to: '/app/transcribe/$transcriptionId', params: { transcriptionId: data.id } })
      } else {
        setResult(data)
      }
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const handleFile = (file: File) => {
    setError(null)
    setResult(null)
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(m.app_transcribe_error_too_large({ max: String(MAX_MB) }))
      return
    }
    setSelectedFile(file)
  }

  const handleCopy = () => {
    if (!result?.text) return
    void navigator.clipboard.writeText(result.text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const updateAudioLevel = useCallback(() => {
    if (!analyserRef.current) return
    const data = new Uint8Array(analyserRef.current.frequencyBinCount)
    analyserRef.current.getByteFrequencyData(data)
    const avg = data.reduce((a, b) => a + b, 0) / data.length
    setAudioLevel(Math.min(100, (avg / 128) * 100))
    // eslint-disable-next-line react-hooks/immutability
    animFrameRef.current = requestAnimationFrame(updateAudioLevel)
  }, [])

  const requestMicAccess = useCallback(async () => {
    setMicBlocked(false)
    setMicPermission('requesting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      setMicPermission('granted')
      try {
        const AudioCtx =
          window.AudioContext ??
          (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
        const audioCtx = new AudioCtx()
        audioContextRef.current = audioCtx
        const analyser = audioCtx.createAnalyser()
        analyser.fftSize = 256
        analyserRef.current = analyser
        audioCtx.createMediaStreamSource(stream).connect(analyser)
      } catch {
        // Audio visualisation unavailable — non-fatal
      }
    } catch {
      setMicPermission('denied')
      // Check if browser has permanently blocked mic (no prompt will ever appear)
      try {
        const status = await navigator.permissions.query({ name: 'microphone' as PermissionName })
        setMicBlocked(status.state === 'denied')
      } catch {
        // Permissions API unavailable — can't determine if permanently blocked
      }
    }
  }, [])

  // Request mic permission when record tab becomes active
  useEffect(() => {
    if (activeTab !== 'record') return
    if (streamRef.current) return // already have stream
    void requestMicAccess()
  }, [activeTab, requestMicAccess])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop()
    }
    setRecording(false)
    if (durationIntervalRef.current) {
      clearInterval(durationIntervalRef.current)
      durationIntervalRef.current = null
    }
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current)
      animFrameRef.current = null
    }
    setAudioLevel(0)
  }, [])

  const startRecording = useCallback(() => {
    setError(null)
    setResult(null)

    if (!streamRef.current || micPermission !== 'granted') {
      setError(m.app_transcribe_record_error_mic())
      return
    }

    const chunks: Blob[] = []
    const recorder = new MediaRecorder(streamRef.current)
    mediaRecorderRef.current = recorder

    recorder.ondataavailable = (e) => chunks.push(e.data)
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: 'audio/webm' })
      transcribeMutation.mutate(new File([blob], 'recording.webm', { type: 'audio/webm' }))
    }

    recorder.start()
    setRecording(true)
    setRecordDuration(0)
    durationIntervalRef.current = window.setInterval(() => setRecordDuration((d) => d + 1), 1000)
    // Resume AudioContext in case it was suspended (can happen when created outside a direct user gesture)
    void audioContextRef.current?.resume().then(updateAudioLevel).catch(updateAudioLevel)
  }, [micPermission, updateAudioLevel, transcribeMutation])

  // Cleanup stream on unmount
  useEffect(
    () => () => {
      stopRecording()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      void audioContextRef.current?.close()
    },
    [stopRecording],
  )

  // Space key shortcut
  useEffect(() => {
    if (activeTab !== 'record') return
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== 'Space') return
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement).tagName)) return
      e.preventDefault()
      if (recording) stopRecording()
      else startRecording()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeTab, recording, startRecording, stopRecording])

  const isTranscribing = transcribeMutation.isPending || retryMutation.isPending

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-base font-semibold text-[var(--color-foreground)]">
          {m.app_transcribe_add_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={() => navigate({ to: '/app/transcribe' })}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_transcribe_back()}
        </Button>
      </div>

      <div className="space-y-6">
        <Card>
          <CardContent className="pt-6 space-y-4">
            {/* Language selector — global setting, shown above tabs */}
            <div className="space-y-1.5">
              <Label htmlFor="language">{m.app_transcribe_language_label()}</Label>
              <Select
                id="language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="max-w-xs"
              >
                <option value="">{m.app_transcribe_language_auto()}</option>
                <option value="nl">{m.app_transcribe_language_nl()}</option>
                <option value="en">{m.app_transcribe_language_en()}</option>
                <option value="de">{m.app_transcribe_language_de()}</option>
                <option value="fr">{m.app_transcribe_language_fr()}</option>
              </Select>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 p-1 bg-[var(--color-muted)]/40 rounded-lg w-fit">
              {(['record', 'upload'] as Tab[]).map((tab) => (
                <button
                  key={tab}
                  onClick={() => {
                    void navigate({ search: { tab: tab === 'record' ? undefined : tab } })
                    setError(null)
                  }}
                  className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                    activeTab === tab
                      ? 'bg-[var(--color-background)] shadow-sm text-[var(--color-foreground)]'
                      : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                  }`}
                >
                  {tab === 'record' ? m.app_transcribe_tab_record() : m.app_transcribe_tab_upload()}
                </button>
              ))}
            </div>

            {/* Record tab */}
            {activeTab === 'record' && (
              <div className="space-y-3">
                {micPermission === 'requesting' && (
                  <p className="text-sm text-[var(--color-muted-foreground)]">
                    {m.app_transcribe_record_permission_request()}
                  </p>
                )}

                {micPermission === 'denied' && (
                  <div className="space-y-2">
                    <p className="text-sm text-[var(--color-destructive)]">
                      {m.app_transcribe_record_permission_denied()}
                    </p>
                    {!micBlocked && (
                      <Button variant="outline" size="sm" onClick={requestMicAccess}>
                        {m.app_transcribe_record_grant_permission()}
                      </Button>
                    )}
                  </div>
                )}

                {(micPermission === 'granted' || micPermission === 'idle') && (
                  <>
                    <div className="flex items-center gap-4">
                      <Button
                        variant={recording ? 'destructive' : 'default'}
                        onClick={() => (recording ? stopRecording() : startRecording())}
                        disabled={isTranscribing || micPermission === 'idle'}
                      >
                        {recording ? (
                          <>
                            <Square className="mr-2 h-4 w-4" />
                            {m.app_transcribe_record_stop()}
                          </>
                        ) : (
                          <>
                            <Mic className="mr-2 h-4 w-4" />
                            {m.app_transcribe_record_start()}
                          </>
                        )}
                      </Button>

                      {recording && (
                        <span className="font-mono text-sm text-[var(--color-muted-foreground)]">
                          {formatDuration(recordDuration)}
                        </span>
                      )}
                    </div>

                    {recording && (
                      <div className="space-y-1.5">
                        <div className="flex items-center gap-2">
                          <div className="h-2 w-2 rounded-full bg-[var(--color-destructive)] animate-pulse" />
                          <span className="text-xs font-medium text-[var(--color-destructive)]">
                            {m.app_transcribe_record_recording()}
                          </span>
                        </div>
                        <div className="h-2 w-full rounded-full bg-[var(--color-muted)] overflow-hidden">
                          <div
                            className="h-full bg-[var(--color-success)] transition-all duration-75"
                            style={{ width: `${audioLevel}%` }}
                          />
                        </div>
                      </div>
                    )}

                    {!recording && !isTranscribing && (
                      <p className="text-xs text-[var(--color-muted-foreground)]">
                        {m.app_transcribe_record_shortcut_hint()}
                      </p>
                    )}
                  </>
                )}
              </div>
            )}

            {/* Upload tab */}
            {activeTab === 'upload' && (
              <div
                className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
                  dragging
                    ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/5'
                    : 'border-[var(--color-border)] hover:border-[var(--color-rl-accent)]/50'
                }`}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  const f = e.dataTransfer.files[0]
                  if (f) handleFile(f)
                }}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED_TYPES}
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) handleFile(f)
                  }}
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
                      {m.app_transcribe_dropzone_hint({ formats: 'WAV, MP3, M4A, OGG, WebM', max: String(MAX_MB) })}
                    </p>
                  </div>
                )}
              </div>
            )}

            {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

            {/* Submit button (upload only) */}
            {activeTab === 'upload' && (
              <div className="flex pt-2">
                <Button
                  onClick={() => selectedFile && transcribeMutation.mutate(selectedFile)}
                  disabled={!selectedFile || isTranscribing}
                >
                  {isTranscribing ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      {m.app_transcribe_processing()}
                    </>
                  ) : (
                    <>
                      <Upload className="mr-2 h-4 w-4" />
                      {m.app_transcribe_submit()}
                    </>
                  )}
                </Button>
              </div>
            )}

            {isTranscribing && (
              <p className="text-xs text-[var(--color-muted-foreground)] text-center">
                {m.app_transcribe_processing_hint()}
              </p>
            )}
          </CardContent>
        </Card>

        {result && result.status === 'failed' && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle>{m.app_transcribe_status_failed()}</CardTitle>
              <CardDescription>{m.app_transcribe_failed_hint()}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex gap-3 pt-2">
                <Button
                  disabled={retryMutation.isPending}
                  onClick={() => retryMutation.mutate(result.id)}
                >
                  {retryMutation.isPending ? (
                    <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{m.app_transcribe_processing()}</>
                  ) : (
                    <><RotateCcw className="mr-2 h-4 w-4" />{m.app_transcribe_retry_button()}</>
                  )}
                </Button>
                <Button
                  variant="outline"
                  disabled={retryMutation.isPending}
                  onClick={() => { setResult(null); setError(null) }}
                >
                  {m.app_transcribe_back()}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {result && result.status === 'transcribed' && result.text && (
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle>{m.app_transcribe_result_title()}</CardTitle>
                  {result.language && result.duration_seconds != null && (
                    <CardDescription>
                      {m.app_transcribe_result_meta({
                        language: result.language.toUpperCase(),
                        duration: formatDuration(result.duration_seconds),
                      })}
                    </CardDescription>
                  )}
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
      </div>
    </div>
  )
}
