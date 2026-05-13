import { useEffect, useRef, useState } from 'react'
import { Loader2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { ACCEPTED_TYPES, MAX_UPLOAD_MB } from '../-add-helpers'

interface FileUploadFormProps {
  active: boolean
  isTranscribing: boolean
  resetToken: number
  onFileReady: () => void
  onSubmit: (file: File) => void
  onError: (message: string) => void
}

export function FileUploadForm({
  active,
  isTranscribing,
  resetToken,
  onFileReady,
  onSubmit,
  onError,
}: FileUploadFormProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)

  useEffect(() => {
    setSelectedFile(null)
  }, [resetToken])

  function handleFile(file: File) {
    onFileReady()
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      onError(m.app_transcribe_error_too_large({ max: String(MAX_UPLOAD_MB) }))
      return
    }
    setSelectedFile(file)
  }

  function submitSelectedFile() {
    if (!selectedFile) return
    onSubmit(selectedFile)
  }

  if (!active) return null

  return (
    <div className="space-y-4">
      <div
        className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
          dragging
            ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/5'
            : 'border-gray-200 hover:border-[var(--color-rl-accent)]/50'
        }`}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          const file = event.dataTransfer.files[0]
          if (file) handleFile(file)
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_TYPES}
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) handleFile(file)
          }}
        />
        <Upload className="mx-auto mb-3 h-8 w-8 text-gray-400" />
        {selectedFile ? (
          <div>
            <p className="font-medium text-sm">{selectedFile.name}</p>
            <p className="text-xs text-gray-400 mt-1">
              {(selectedFile.size / 1024 / 1024).toFixed(1)} MB
            </p>
          </div>
        ) : (
          <div>
            <p className="text-sm font-medium">{m.app_transcribe_dropzone_label()}</p>
            <p className="text-xs text-gray-400 mt-1">
              {m.app_transcribe_dropzone_hint({
                formats: 'WAV, MP3, M4A, OGG, WebM',
                max: String(MAX_UPLOAD_MB),
              })}
            </p>
          </div>
        )}
      </div>

      <div className="flex pt-2">
        <Button
          onClick={submitSelectedFile}
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
    </div>
  )
}
