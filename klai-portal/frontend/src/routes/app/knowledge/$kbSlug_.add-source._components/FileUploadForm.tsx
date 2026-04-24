import { useState, useCallback, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Upload, FileText, CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { DOCS_BASE, getOrgSlug } from '@/lib/kb-editor/tree-utils'

export interface FileUploadFormProps {
  kbSlug: string
  onBack: () => void
}

export function FileUploadForm({ kbSlug, onBack }: FileUploadFormProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const orgSlug = getOrgSlug()

  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [isDragOver, setIsDragOver] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length > 0) setSelectedFiles((prev) => [...prev, ...files])
  }, [])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }, [])

  const onDragLeave = useCallback(() => {
    setIsDragOver(false)
  }, [])

  const fileUploadMutation = useMutation({
    mutationFn: async () => {
      for (const file of selectedFiles) {
        const formData = new FormData()
        formData.append('file', file)
        await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/upload`, {
          method: 'POST',
          body: formData,
        })
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-items', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['personal-knowledge', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      setUploadSuccess(true)
      setSelectedFiles([])
      setTimeout(() => {
        void navigate({
          to: '/app/knowledge/$kbSlug/overview',
          params: { kbSlug },
        })
      }, 1500)
    },
  })

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
        <p className="text-xs text-gray-400 mt-2">
          PDF, Word, Excel, PowerPoint, TXT, Markdown, CSV
        </p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.doc,.docx,.xls,.xlsx,.pptx,.txt,.md,.csv"
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            if (files.length > 0) setSelectedFiles((prev) => [...prev, ...files])
            e.target.value = ''
          }}
        />
      </div>

      {/* Selected files list */}
      {selectedFiles.length > 0 && (
        <div className="space-y-1">
          {selectedFiles.map((file, i) => (
            <div
              key={`${file.name}-${i}`}
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

      {/* Error banner */}
      {fileUploadMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {fileUploadMutation.error instanceof Error
            ? fileUploadMutation.error.message
            : m.knowledge_add_source_file_error_generic()}
        </p>
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
