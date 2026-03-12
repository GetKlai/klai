import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useRef, useState, useEffect, useCallback } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { ArrowLeft, Upload, Copy, CheckCheck, Loader2, Mic, Square } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/transcribe/add')({
  component: AddTranscribePage,
})

const SCRIBE_BASE = '/scribe/v1'
const ACCEPTED_TYPES = '.wav,.mp3,.m4a,.ogg,.webm'
const MAX_MB = 100

type Tab = 'record' | 'upload'

interface TranscriptionDraft {
  name?: string | null
  text: string
  language: string
  duration_seconds: number
  inference_time_seconds: number | null
  provider: string
  model: string
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
  const navigate = useNavigate()

  const [activeTab, setActiveTab] = useState<Tab>('record')
  const [language, setLanguage] = useState<string>('')
  const [result, setResult] = useState<TranscriptionDraft | null>(null)
  const [name, setName] = useState('')
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Upload tab
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)

  // Record tab
  const [micPermission, setMicPermission] = useState<'idle' | 'requesting' | 'granted' | 'denied'>('idle')
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
      const res = await fetch(`${SCRIBE_BASE}/transcribe`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body?.detail ?? m.app_transcribe_error_generic())
      }
      return res.json() as Promise<TranscriptionDraft>
    },
    onSuccess: (data) => {
      setResult(data)
      setName('')
      setSelectedFile(null)
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const saveMutation = useMutation({
    mutationFn: async (draft: TranscriptionDraft) => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body?.detail ?? m.app_transcribe_error_generic())
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcriptions', token] })
      navigate({ to: '/app/transcribe' })
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const handleFile = (file: File) => {
    setError(null)
    setResult(null)
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(m.app_transcribe_error_too_large({ max: MAX_MB }))
      return
    }
    setSelectedFile(file)
  }

  const handleCopy = () => {
    if (!result) return
    navigator.clipboard.writeText(result.text).then(() => {
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
    animFrameRef.current = requestAnimationFrame(updateAudioLevel)
  }, [])

  // Request mic permission when record tab becomes active
  useEffect(() => {
    if (activeTab !== 'record') return
    if (streamRef.current) return // already have stream

    setMicPermission('requesting')
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
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
      })
      .catch(() => setMicPermission('denied'))
  }, [activeTab])

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
    updateAudioLevel()
  }, [micPermission, updateAudioLevel, transcribeMutation])

  // Cleanup stream on unmount
  useEffect(
    () => () => {
      stopRecording()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      audioContextRef.current?.close()
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

  const isTranscribing = transcribeMutation.isPending

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
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
                <option value="nl">Nederlands</option>
                <option value="en">English</option>
                <option value="de">Deutsch</option>
                <option value="fr">Français</option>
              </Select>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 p-1 bg-[var(--color-muted)]/40 rounded-lg w-fit">
              {(['record', 'upload'] as Tab[]).map((tab) => (
                <button
                  key={tab}
                  onClick={() => {
                    setActiveTab(tab)
                    setError(null)
                  }}
                  className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                    activeTab === tab
                      ? 'bg-white shadow-sm text-[var(--color-purple-deep)]'
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
                  <p className="text-sm text-[var(--color-destructive)]">
                    {m.app_transcribe_record_permission_denied()}
                  </p>
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
                          <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
                          <span className="text-xs font-medium text-red-600">
                            {m.app_transcribe_record_recording()}
                          </span>
                        </div>
                        <div className="h-2 w-full rounded-full bg-[var(--color-muted)] overflow-hidden">
                          <div
                            className="h-full bg-green-500 transition-all duration-75"
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
                    ? 'border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5'
                    : 'border-[var(--color-border)] hover:border-[var(--color-purple-accent)]/50'
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
                      {m.app_transcribe_dropzone_hint({ formats: 'WAV, MP3, M4A, OGG, WebM', max: MAX_MB })}
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
            <CardContent className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="txn-name">{m.app_transcribe_name_label()}</Label>
                <Input
                  id="txn-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={m.app_transcribe_name_placeholder()}
                  disabled={saveMutation.isPending}
                />
              </div>
              <p className="text-sm whitespace-pre-wrap leading-relaxed">{result.text}</p>
              <div className="flex gap-3 pt-2">
                <Button
                  disabled={saveMutation.isPending}
                  onClick={() => saveMutation.mutate({ ...result, name: name.trim() || null })}
                >
                  {saveMutation.isPending ? (
                    <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{m.app_transcribe_processing()}</>
                  ) : m.app_transcribe_save_button()}
                </Button>
                <Button
                  variant="outline"
                  disabled={saveMutation.isPending}
                  onClick={() => { setResult(null); setName(''); setError(null) }}
                >
                  {m.app_transcribe_discard_button()}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
