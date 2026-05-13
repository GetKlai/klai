import { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { formatDuration } from '../-add-helpers'

interface RecordingFormProps {
  active: boolean
  isTranscribing: boolean
  onBeforeRecord: () => void
  onRecordedFile: (file: File) => void
  onError: (message: string) => void
}

export function RecordingForm({
  active,
  isTranscribing,
  onBeforeRecord,
  onRecordedFile,
  onError,
}: RecordingFormProps) {
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
        // Audio visualisation unavailable - non-fatal.
      }
    } catch {
      setMicPermission('denied')
      try {
        const status = await navigator.permissions.query({ name: 'microphone' as PermissionName })
        setMicBlocked(status.state === 'denied')
      } catch {
        // Permissions API unavailable, so the UI falls back to retry.
      }
    }
  }, [])

  useEffect(() => {
    if (!active) return
    if (streamRef.current) return
    void requestMicAccess()
  }, [active, requestMicAccess])

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
    onBeforeRecord()

    if (!streamRef.current || micPermission !== 'granted') {
      onError(m.app_transcribe_record_error_mic())
      return
    }

    const chunks: Blob[] = []
    const recorder = new MediaRecorder(streamRef.current)
    mediaRecorderRef.current = recorder

    recorder.ondataavailable = (e) => chunks.push(e.data)
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: 'audio/webm' })
      onRecordedFile(new File([blob], 'recording.webm', { type: 'audio/webm' }))
    }

    recorder.start()
    setRecording(true)
    setRecordDuration(0)
    durationIntervalRef.current = window.setInterval(() => setRecordDuration((d) => d + 1), 1000)
    void audioContextRef.current?.resume().then(updateAudioLevel).catch(updateAudioLevel)
  }, [micPermission, onBeforeRecord, onError, onRecordedFile, updateAudioLevel])

  useEffect(
    () => () => {
      stopRecording()
      streamRef.current?.getTracks().forEach((track) => track.stop())
      void audioContextRef.current?.close()
    },
    [stopRecording],
  )

  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== 'Space') return
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((event.target as HTMLElement).tagName)) return
      event.preventDefault()
      if (recording) stopRecording()
      else startRecording()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, recording, startRecording, stopRecording])

  if (!active) return null

  if (micPermission === 'requesting') {
    return (
      <p className="text-sm text-gray-400">
        {m.app_transcribe_record_permission_request()}
      </p>
    )
  }

  if (micPermission === 'denied') {
    return (
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
    )
  }

  return (
    <div className="space-y-3">
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
          <span className="font-mono text-sm text-gray-400">
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
        <p className="text-xs text-gray-400">
          {m.app_transcribe_record_shortcut_hint()}
        </p>
      )}
    </div>
  )
}
