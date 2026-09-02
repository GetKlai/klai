import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { FileText, Upload } from 'lucide-react'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { invalidateKnowledgeSourceLists } from '@/lib/kb-query-keys'
import {
  UPLOAD_ACCEPT_ATTR,
  UPLOAD_FORMATS_LABEL,
  uploadReasonToMessage,
} from '@/lib/upload-error-messages'

// SPEC-KB-FILE-UPLOAD-001 - full whitelist routed through portal-api.
// .md / .txt / .csv go to /ingest/v1/document directly. PDF / DOCX /
// XLSX / PPTX / JSON / XML go to docling-serve's async queue; portal-api
// returns 202 with status="processing" and we poll until terminal.
// .zip / .tar / .doc come back as `phase_pending` (UI shows
// "binnenkort"); they ship in a follow-up.

const MAX_FILE_BYTES = 200 * 1024 * 1024 // 200 MB - matches Caddy + portal-api
const MAX_FILES_PER_UPLOAD = 10
const POLL_INTERVAL_MS = 2000
const POLL_TIMEOUT_MS = 10 * 60 * 1000 // 10 min - covers a 100 MB PDF on CPU docling

export interface FileUploadFormProps {
  kbSlug: string
  onBack: () => void
}

interface ServerSkippedEntry {
  filename: string
  reason: string
  extension?: string | null
}

interface ServerUploadEntry {
  id: string
  filename: string
  status: 'done' | 'processing' | 'ingesting' | 'failed'
  source_type: string
  source_ref: string
  artifact_id: string | null
  failure_reason?: string | null
}

interface ServerUploadResponse {
  uploads: ServerUploadEntry[]
  skipped: ServerSkippedEntry[]
}

interface ClientRejection {
  filename: string
  reason: 'oversize' | 'no_file_selected' | 'too_many_files'
  size?: number
}

export function partitionClientSide(files: File[], existingCount = 0): {
  ok: File[]
  rejected: ClientRejection[]
} {
  const ok: File[] = []
  const rejected: ClientRejection[] = []
  for (const f of files) {
    if (existingCount + ok.length >= MAX_FILES_PER_UPLOAD) {
      rejected.push({ filename: f.name, reason: 'too_many_files', size: f.size })
      continue
    }
    if (f.size > MAX_FILE_BYTES) {
      rejected.push({ filename: f.name, reason: 'oversize', size: f.size })
      continue
    }
    ok.push(f)
  }
  return { ok, rejected }
}

function fileSizeLabel(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

interface DisplayEntry {
  id: string
  filename: string
  status: 'done' | 'processing' | 'ingesting' | 'failed'
  failureReason?: string | null
}

export function FileUploadForm({ kbSlug, onBack }: FileUploadFormProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [clientRejections, setClientRejections] = useState<ClientRejection[]>([])
  const [serverSkipped, setServerSkipped] = useState<ServerSkippedEntry[]>([])
  const [trackedEntries, setTrackedEntries] = useState<DisplayEntry[]>([])
  const [isDragOver, setIsDragOver] = useState(false)
  const [allDone, setAllDone] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addFiles = useCallback((incoming: File[]) => {
    const { ok, rejected } = partitionClientSide(incoming, selectedFiles.length)
    if (ok.length > 0) setSelectedFiles((prev) => [...prev, ...ok])
    if (rejected.length > 0) setClientRejections((prev) => [...prev, ...rejected])
  }, [selectedFiles.length])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragOver(false)
      addFiles(Array.from(e.dataTransfer.files))
    },
    [addFiles],
  )
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }, [])
  const onDragLeave = useCallback(() => setIsDragOver(false), [])

  const fileUploadMutation = useMutation({
    mutationFn: async (): Promise<ServerUploadResponse> => {
      const formData = new FormData()
      for (const file of selectedFiles) formData.append('files', file)
      return apiFetch<ServerUploadResponse>(
        `/api/app/knowledge-bases/${kbSlug}/sources/file`,
        { method: 'POST', body: formData },
      )
    },
    onSuccess: (data) => {
      invalidateKnowledgeSourceLists(queryClient, kbSlug)
      setServerSkipped(data.skipped)
      setSelectedFiles([])
      setTrackedEntries(
        data.uploads.map((u) => ({
          id: u.id,
          filename: u.filename,
          status: u.status,
          failureReason: u.failure_reason ?? null,
        })),
      )
    },
  })

  // Status polling: while any tracked entry is still ``processing`` or
  // ``ingesting``, poll the per-row status endpoint every POLL_INTERVAL_MS.
  // The poll loop terminates when every entry reaches a terminal state
  // OR when POLL_TIMEOUT_MS elapses (operator/escalation case).
  useEffect(() => {
    if (trackedEntries.length === 0) return
    const pendingIds = trackedEntries
      .filter((e) => e.status === 'processing' || e.status === 'ingesting')
      .map((e) => e.id)
    if (pendingIds.length === 0) {
      // All terminal - show success when at least one done, no failures.
      const anyFailed = trackedEntries.some((e) => e.status === 'failed')
      const anyDone = trackedEntries.some((e) => e.status === 'done')
      if (anyDone && !anyFailed && serverSkipped.length === 0) {
        setAllDone(true)
        const t = setTimeout(() => {
          void navigate({
            to: '/app/knowledge/$kbSlug/sources',
            params: { kbSlug },
          })
        }, 1500)
        return () => clearTimeout(t)
      }
      return
    }

    let cancelled = false
    const start = Date.now()

    const tick = async (): Promise<void> => {
      if (cancelled) return
      if (Date.now() - start > POLL_TIMEOUT_MS) {
        // Operator escalation case - flag remaining as failed in the UI
        // so the user is not stuck on a spinner forever.
        setTrackedEntries((prev) =>
          prev.map((e) =>
            e.status === 'processing' || e.status === 'ingesting'
              ? { ...e, status: 'failed', failureReason: 'docling_timeout' }
              : e,
          ),
        )
        return
      }

      const updates = await Promise.all(
        pendingIds.map(async (id) => {
          try {
            return await apiFetch<ServerUploadEntry>(
              `/api/app/knowledge-bases/${kbSlug}/sources/file/${id}/status`,
              { method: 'GET' },
            )
          } catch {
            return null
          }
        }),
      )
      if (cancelled) return
      setTrackedEntries((prev) =>
        prev.map((e) => {
          const update = updates.find((u) => u && u.id === e.id) ?? null
          if (!update) return e
          return {
            ...e,
            status: update.status,
            failureReason: update.failure_reason ?? null,
          }
        }),
      )
    }

    const handle = setInterval(() => void tick(), POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(handle)
    }
  }, [trackedEntries, kbSlug, serverSkipped.length, navigate])

  // Map the mutation error to a friendly message, parsing structured
  // error_code + skipped[] from the backend.
  const errorMessage = (() => {
    if (!fileUploadMutation.error) return null
    if (fileUploadMutation.error instanceof ApiError) {
      try {
        const parsed = JSON.parse(fileUploadMutation.error.detail) as {
          error_code?: string
          skipped?: ServerSkippedEntry[]
        }
        // Stash skipped[] from the rejection-response so the UI can
        // render the same per-file rail as for partial successes.
        if (parsed.skipped && parsed.skipped.length > 0) {
          if (serverSkipped.length === 0) setServerSkipped(parsed.skipped)
        }
        if (parsed.error_code) return uploadReasonToMessage(parsed.error_code)
      } catch {
        // Fall through.
      }
      return uploadReasonToMessage('default')
    }
    return uploadReasonToMessage('default')
  })()

  return (
    <div className="space-y-6">
      {/* Success banner */}
      {allDone && (
        <Alert variant="success">
          <p>
            {m.knowledge_add_source_file_success()}
          </p>
        </Alert>
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
            : 'border-gray-200 hover:border-gray-300 klai-hover/50'
        }`}
      >
        <Upload className="h-8 w-8 text-gray-300 mx-auto mb-3" />
        <p className="text-sm font-medium text-gray-900">
          {m.knowledge_add_source_file_drop_hint()}
        </p>
        <p className="text-xs text-gray-600 mt-2">{UPLOAD_FORMATS_LABEL} (max 200 MB per bestand)</p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={UPLOAD_ACCEPT_ATTR}
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            addFiles(files)
            e.target.value = ''
          }}
        />
      </div>

      {/* Selected files list (pre-submit) */}
      {selectedFiles.length > 0 && (
        <div className="space-y-1">
          {selectedFiles.map((file, i) => (
            <div
              key={`${file.name}-${String(i)}`}
              className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-2.5"
            >
              <FileText className="h-4 w-4 text-gray-500 shrink-0" />
              <span className="flex-1 truncate text-sm text-gray-900">{file.name}</span>
              <span className="text-xs text-gray-600 shrink-0">{fileSizeLabel(file.size)}</span>
              <button
                type="button"
                aria-label={`Remove ${file.name}`}
                onClick={(e) => {
                  e.stopPropagation()
                  setSelectedFiles((prev) => prev.filter((_, j) => j !== i))
                }}
                className="text-xs text-gray-500 hover:text-[var(--color-destructive)] transition-colors"
              >
                &times;
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Tracked entries (post-submit, with status) */}
      {trackedEntries.length > 0 && (
        <div className="space-y-1">
          {trackedEntries.map((entry) => (
            <div
              key={entry.id}
              className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-2.5"
            >
              <FileText className="h-4 w-4 text-gray-500 shrink-0" />
              <span className="flex-1 truncate text-sm text-gray-900">{entry.filename}</span>
              {entry.status === 'done' && (
                <span className="text-xs text-[var(--color-success-text)] shrink-0">Verwerkt</span>
              )}
              {(entry.status === 'processing' || entry.status === 'ingesting') && (
                <span className="text-xs text-gray-600 shrink-0">Bezig met verwerken…</span>
              )}
              {entry.status === 'failed' && (
                <span className="text-xs text-[var(--color-destructive)] shrink-0">
                  {uploadReasonToMessage(entry.failureReason ?? 'default')}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Client-side rejections */}
      {clientRejections.length > 0 && (
        <div className="rounded-lg border border-[var(--color-destructive)] p-3">
          <p className="text-sm font-medium text-[var(--color-destructive)] mb-1">
            Niet toegevoegd:
          </p>
          <ul className="text-xs text-[var(--color-destructive)] space-y-0.5">
            {clientRejections.map((r, i) => (
              <li key={`${r.filename}-${String(i)}`}>
                {r.filename} - {uploadReasonToMessage(r.reason)}
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setClientRejections([])}
            className="mt-2 text-xs text-gray-600 hover:text-gray-900"
          >
            Sluiten
          </button>
        </div>
      )}

      {/* Server-side per-file skipped */}
      {serverSkipped.length > 0 && (
        <div className="rounded-lg border border-[var(--color-warning)] bg-[var(--color-warning-bg)] p-3">
          <p className="text-sm font-medium text-[var(--color-warning-text)] mb-1">Niet verwerkt:</p>
          <ul className="text-xs text-[var(--color-warning-text)] space-y-0.5">
            {serverSkipped.map((r, i) => (
              <li key={`${r.filename}-${String(i)}`}>
                {r.filename} - {uploadReasonToMessage(r.reason)}
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
          className="text-sm text-gray-600 hover:text-gray-900 transition-colors"
        >
          {m.knowledge_add_source_back()}
        </button>
      </div>
    </div>
  )
}
