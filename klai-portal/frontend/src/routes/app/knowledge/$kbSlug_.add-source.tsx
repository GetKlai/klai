import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useCallback, useRef } from 'react'
import { ArrowLeft, FileUp, Globe, Type, Upload, FileText, CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { DOCS_BASE, getOrgSlug } from '@/lib/kb-editor/tree-utils'

// -- Route -------------------------------------------------------------------

export const Route = createFileRoute('/app/knowledge/$kbSlug_/add-source')({
  component: AddSourcePage,
})

// -- Types -------------------------------------------------------------------

type ActiveTab = 'file' | 'url' | 'text'

// -- Main component ----------------------------------------------------------

function AddSourcePage() {
  const { kbSlug } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const orgSlug = getOrgSlug()

  const [activeTab, setActiveTab] = useState<ActiveTab>('file')

  // File tab state
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [isDragOver, setIsDragOver] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // URL tab state (greyed out — endpoint does not exist)
  const [urlValue, setUrlValue] = useState('')

  // Text tab state (greyed out — endpoint does not exist)
  const [textTitle, setTextTitle] = useState('')
  const [textContent, setTextContent] = useState('')

  function goBack() {
    void navigate({
      to: '/app/knowledge/$kbSlug/overview',
      params: { kbSlug },
    })
  }

  // -- File drop handlers ---------------------------------------------------

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

  // -- File upload mutation -------------------------------------------------
  // Uses the docs-service upload endpoint: POST FormData to /api/docs/api/orgs/{org}/kbs/{kb}/upload
  // This is the only verified upload endpoint in the current backend.

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

  // -- Tab config -----------------------------------------------------------

  const tabs: { id: ActiveTab; label: () => string; icon: React.ComponentType<{ className?: string }>; comingSoon: boolean }[] = [
    { id: 'file', label: m.knowledge_add_source_tab_file, icon: FileUp, comingSoon: false },
    { id: 'url', label: m.knowledge_add_source_tab_url, icon: Globe, comingSoon: true },
    { id: 'text', label: m.knowledge_add_source_tab_text, icon: Type, comingSoon: true },
  ]

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Page header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.knowledge_add_source_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.knowledge_add_source_back()}
        </Button>
      </div>
      <p className="text-sm text-gray-400 mb-6">
        {m.knowledge_add_source_subtitle()}
      </p>

      {/* Tab navigation */}
      <div className="flex items-center gap-1 border-b border-gray-200 mb-6">
        {tabs.map(({ id, label, comingSoon }) => {
          if (comingSoon) {
            return (
              <Tooltip key={id} label={m.knowledge_add_source_coming_soon()}>
                <button
                  type="button"
                  disabled
                  className="px-4 py-2.5 text-sm font-medium text-gray-300 cursor-not-allowed select-none border-b-2 border-transparent"
                >
                  {label()}
                </button>
              </Tooltip>
            )
          }
          return (
            <button
              key={id}
              type="button"
              onClick={() => setActiveTab(id)}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === id
                  ? 'border-gray-900 text-gray-900'
                  : 'border-transparent text-gray-400 hover:text-gray-700'
              }`}
            >
              {label()}
            </button>
          )
        })}
      </div>

      {/* ── File upload tab ─────────────────────────────────────────── */}
      {activeTab === 'file' && (
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
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click() }}
            aria-label={m.knowledge_add_source_file_drop_hint()}
            className={`cursor-pointer rounded-xl border-2 border-dashed py-14 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] ${
              isDragOver ? 'border-gray-400 bg-gray-50' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/50'
            }`}
          >
            <Upload className="h-8 w-8 text-gray-300 mx-auto mb-3" />
            <p className="text-sm font-medium text-gray-900">
              {m.knowledge_add_source_file_drop_hint()}
            </p>
            <p className="text-xs text-gray-400 mt-2">PDF, Word, Excel, PowerPoint, TXT, Markdown, CSV</p>
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
                // Reset input so the same file can be re-selected after removal
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
              onClick={goBack}
              className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
            >
              {m.knowledge_add_source_back()}
            </button>
          </div>
        </div>
      )}

      {/* ── URL tab (coming soon) ───────────────────────────────────── */}
      {activeTab === 'url' && (
        <div className="space-y-4 opacity-50 pointer-events-none select-none">
          <div className="space-y-1.5">
            <label htmlFor="url-input" className="block text-sm font-medium text-gray-900">
              {m.knowledge_add_source_url_label()}
            </label>
            <input
              id="url-input"
              type="url"
              disabled
              placeholder="https://example.com"
              value={urlValue}
              onChange={(e) => setUrlValue(e.target.value)}
              className="flex w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 disabled:cursor-not-allowed"
            />
            <p className="text-xs text-gray-400">{m.knowledge_add_source_url_hint()}</p>
          </div>
          <p className="text-sm font-medium text-gray-400">{m.knowledge_add_source_coming_soon()}</p>
        </div>
      )}

      {/* ── Text tab (coming soon) ──────────────────────────────────── */}
      {activeTab === 'text' && (
        <div className="space-y-4 opacity-50 pointer-events-none select-none">
          <div className="space-y-1.5">
            <label htmlFor="text-title" className="block text-sm font-medium text-gray-900">
              {m.knowledge_add_source_text_title_label()}
            </label>
            <input
              id="text-title"
              type="text"
              disabled
              placeholder="Titel"
              value={textTitle}
              onChange={(e) => setTextTitle(e.target.value)}
              className="flex w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 disabled:cursor-not-allowed"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="text-content" className="block text-sm font-medium text-gray-900">
              {m.knowledge_add_source_text_content_label()}
            </label>
            <textarea
              id="text-content"
              disabled
              placeholder="Plak of typ je tekst hier…"
              value={textContent}
              onChange={(e) => setTextContent(e.target.value)}
              className="flex min-h-[160px] w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 disabled:cursor-not-allowed"
            />
          </div>
          <p className="text-sm font-medium text-gray-400">{m.knowledge_add_source_coming_soon()}</p>
        </div>
      )}
    </div>
  )
}
