import { useCallback, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CheckCircle2, FileText, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { apiFetch, ApiError } from '@/lib/apiFetch'

// SPEC-KB-FILE-UPLOAD-001 Phase 1A: text formats only via the new
// /api/app/knowledge-bases/{kb}/sources/file route. Binary formats
// (.pdf .docx .pptx .xlsx etc.) ship in Phase 1B+; the backend returns
// `phase_pending` per file in the `skipped` array which we surface as
// a localised "binnenkort" message. The previous wiring against
// DOCS_BASE (klai-docs/Gitea) was wrong: that endpoint only accepted
// .md and crashed on 100MB+ binaries.

const PHASE_1_ACCEPT = '.md,.txt,.csv'
const PHASE_1_MAX_BYTES = 10 * 1024 * 1024 // 10 MB — matches backend MAX_TEXT_FILE_BYTES
const PHASE_1_FORMATS_LABEL = 'Markdown, TXT, CSV'

export interface FileUploadFormProps {
  kbSlug: string
  onBack: () => void
}

interface SkippedEntry {
  filename: string
  reason: string
  extension?: string | null
}

interface UploadResponse {
  uploads: Array<{ artifact_id: string; source_ref: string; source_type: string }>
  skipped: SkippedEntry[]
}

interface ClientRejection {
  filename: string
  reason: 'oversize' | 'unsupported_extension'
  size?: number
}

const SUPPORTED_EXTS = new Set(['.md', '.txt', '.csv'])

function getExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

function partitionClientSide(files: File[]): { ok: File[]; rejected: ClientRejection[] } {
  const ok: File[] = []
  const rejected: ClientRejection[] = []
  for (const f of files) {
    const ext = getExtension(f.name)
    if (!SUPPORTED_EXTS.has(ext)) {
      rejected.push({ filename: f.name, reason: 'unsupported_extension' })
      continue
    }
    if (f.size > PHASE_1_MAX_BYTES) {
      rejected.push({ filename: f.name, reason: 'oversize', size: f.size })
      continue
    }
    ok.push(f)
  }
  return { ok, rejected }
}

function reasonToMessage(reason: string): string {
  switch (reason) {
    case 'unsupported_extension':
    case 'phase_pending':
      return `Bestandstype niet ondersteund. Phase 1 ondersteunt: ${PHASE_1_FORMATS_LABEL}.`
    case 'file_too_large':
    case 'oversize':
      return 'Bestand te groot (max 10 MB voor tekst).'
    case 'invalid_text_encoding':
      return 'Bestand kon niet worden gedecodeerd (geen UTF-8 of Windows-1252).'
    case 'empty_content':
      return 'Bestand is leeg.'
    case 'kb_quota_items_exceeded':
      return 'Geen ruimte meer in deze kennisbank.'
    case 'no_files':
      return 'Geen bestand geselecteerd.'
    case 'too_many_files':
      return 'Te veel bestanden geselecteerd (max 10 per upload).'
    default:
      return 'Upload mislukt. Probeer opnieuw.'
  }
}

export function FileUploadForm({ kbSlug, onBack }: FileUploadFormProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [clientRejections, setClientRejections] = useState<ClientRejection[]>([])
  const [serverSkipped, setServerSkipped] = useState<SkippedEntry[]>([])
  const [isDragOver, setIsDragOver] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addFiles = useCallback((incoming: File[]) => {
    const { ok, rejected } = partitionClientSide(incoming)
    if (ok.length > 0) setSelectedFiles((prev) => [...prev, ...ok])
    if (rejected.length > 0) setClientRejections((prev) => [...prev, ...rejected])
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    addFiles(Array.from(e.dataTransfer.files))
  }, [addFiles])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }, [])

  const onDragLeave = useCallback(() => {
    setIsDragOver(false)
  }, [])

  const fileUploadMutation = useMutation({
    mutationFn: async (): Promise<UploadResponse> => {
      const formData = new FormData()
      for (const file of selectedFiles) formData.append('files', file)
      // SPEC-KB-FILE-UPLOAD-001: new portal-api endpoint, NOT the
      // klai-docs wiki route. Multi-file in one request — backend
      // partial-success semantics live in the response.
      return apiFetch<UploadResponse>(
        `/api/app/knowledge-bases/${kbSlug}/sources/file`,
        { method: 'POST', body: formData }
      )
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['kb-items', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['personal-knowledge', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      setServerSkipped(data.skipped)
      setSelectedFiles([])
      // Only show full success when at least one file was accepted AND no skips.
      if (data.uploads.length > 0 && data.skipped.length === 0) {
        setUploadSuccess(true)
        setTimeout(() => {
          void navigate({
            to: '/app/knowledge/$kbSlug/overview',
            params: { kbSlug },
          })
        }, 1500)
      }
    },
  })

  const errorMessage = (() => {
    if (!fileUploadMutation.error) return null
    if (fileUploadMutation.error instanceof ApiError) {
      try {
        const parsed = JSON.parse(fileUploadMutation.error.detail) as { error_code?: string }
        if (parsed.error_code) return reasonToMessage(parsed.error_code)
      } catch {
        // Fall through to generic message.
      }
      return reasonToMessage('default')
    }
    return reasonToMessage('default')
  })()

  return (
    <div className="space-y-6">
      {/* Success banner */}
      {uploadSuccess && (
        <div className="flex items-center gap-2 rounded-lg border border-[var(--color-success)] bg-[var(--color-success-bg)] px-4 py-3">
          <CheckCircle2 className="h-4 w-4 text-[var(--color-success)] shrink-0" />
          <p className="text-sm text-[var(--color-success-text)]">
            {m.knowledge_add_source_file_success()}
          </p>
        </div>
      )}

      {/* Drop zone */}
      <div
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click()
        }}
        aria-label={m.knowledge_add_source_file_drop_hint()}
        className={`cursor-pointer rounded-xl border-2 border-dashed py-14 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] ${
          isDragOver
            ? 'border-gray-400 bg-gray-50'
            : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/50'
        }`}
      >
        <Upload className="h-8 w-8 text-gray-300 mx-auto mb-3" />
        <p className="text-sm font-medium text-gray-900">
          {m.knowledge_add_source_file_drop_hint()}
        </p>
        <p className="text-xs text-gray-400 mt-2">{PHASE_1_FORMATS_LABEL} (max 10 MB per bestand)</p>
        <p className="text-xs text-gray-400 mt-1">PDF, Word, Excel, PowerPoint volgen binnenkort</p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={PHASE_1_ACCEPT}
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            addFiles(files)
            e.target.value = ''
          }}
        />
      </div>

      {/* Selected files list */}
      {selectedFiles.length > 0 && (
        <div className="space-y-1">
          {selectedFiles.map((file, i) => (
            <div
              key={`${file.name}-${String(i)}`}
              className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-2.5"
            >
              <FileText className="h-4 w-4 text-gray-400 shrink-0" />
              <span className="flex-1 truncate text-sm text-gray-900">{file.name}</span>
              <span className="text-xs text-gray-400 shrink-0">
                {(file.size / 1024).toFixed(0)} KB
              </span>
              <button
                type="button"
                aria-label={`Remove ${file.name}`}
                onClick={(e) => {
                  e.stopPropagation()
                  setSelectedFiles((prev) => prev.filter((_, j) => j !== i))
                }}
                className="text-xs text-gray-400 hover:text-[var(--color-destructive)] transition-colors"
              >
                &times;
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Client-side rejections */}
      {clientRejections.length > 0 && (
        <div className="rounded-lg border border-[var(--color-destructive)] bg-[var(--color-destructive-bg,_transparent)] p-3">
          <p className="text-sm font-medium text-[var(--color-destructive)] mb-1">
            Niet toegevoegd:
          </p>
          <ul className="text-xs text-[var(--color-destructive)] space-y-0.5">
            {clientRejections.map((r, i) => (
              <li key={`${r.filename}-${String(i)}`}>
                {r.filename} — {reasonToMessage(r.reason)}
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setClientRejections([])}
            className="mt-2 text-xs text-gray-400 hover:text-gray-900"
          >
            Sluiten
          </button>
        </div>
      )}

      {/* Server-side per-file skipped (after upload completes) */}
      {serverSkipped.length > 0 && (
        <div className="rounded-lg border border-[var(--color-warning)] bg-[var(--color-warning-bg)] p-3">
          <p className="text-sm font-medium text-[var(--color-warning)] mb-1">
            Niet verwerkt:
          </p>
          <ul className="text-xs text-[var(--color-warning)] space-y-0.5">
            {serverSkipped.map((r, i) => (
              <li key={`${r.filename}-${String(i)}`}>
                {r.filename} — {reasonToMessage(r.reason)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Error banner */}
      {errorMessage && (
        <p className="text-sm text-[var(--color-destructive)]">{errorMessage}</p>
      )}

      {/* Actions */}
      <div className="flex items-center gap-3 pt-2">
        <Button
          type="button"
          disabled={selectedFiles.length === 0 || fileUploadMutation.isPending}
          onClick={() => fileUploadMutation.mutate()}
        >
          {fileUploadMutation.isPending
            ? m.knowledge_add_source_file_uploading()
            : `Upload${selectedFiles.length > 0 ? ` (${String(selectedFiles.length)})` : ''}`}
        </Button>
        <button
          type="button"
          onClick={onBack}
          className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
        >
          {m.knowledge_add_source_back()}
        </button>
      </div>
    </div>
  )
}
