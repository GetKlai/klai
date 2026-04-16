import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, ChevronRight, ChevronDown, AlertTriangle, CheckCircle2,
  Loader2, Sparkles, Globe, FileText, FileUp, Type, Settings, Upload,
  Image, Rss, MessageSquare, Table,
} from 'lucide-react'
import {
  SiGithub, SiNotion, SiGoogledrive, SiYoutube, SiGmail,
  SiGooglesheets, SiConfluence, SiAirtable,
} from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect, type MultiSelectOption } from '@/components/ui/multi-select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

// -- Types -------------------------------------------------------------------

type SourceType =
  | 'file' | 'url' | 'text' | 'image'
  | 'web_crawler' | 'youtube' | 'rss'
  | 'google_drive' | 'gmail' | 'google_sheets'
  | 'github' | 'notion' | 'confluence' | 'slack' | 'airtable'

interface SourceTypeOption {
  type: SourceType
  label: () => string
  description: () => string
  Icon: React.ComponentType<{ className?: string }>
  comingSoon?: boolean
}

interface GitHubConfig {
  installation_id: string
  repo_owner: string
  repo_name: string
  branch: string
  path_filter: string
}

interface WebCrawlerConfig {
  base_url: string
  path_prefix: string
  max_pages: string
  content_selector: string
}

interface NotionConfig {
  access_token: string
  database_ids: string
  max_pages: string
}

type WcStep = 'details' | 'preview' | 'settings'

const ASSERTION_MODE_OPTIONS: MultiSelectOption[] = [
  { value: 'factual',    label: 'Fact',        description: 'Established fact, documentation, specs' },
  { value: 'procedural', label: 'Procedure',   description: "Step-by-step instructions, how-to's" },
  { value: 'belief',     label: 'Claim',       description: 'Not conclusively proven claim' },
  { value: 'quoted',     label: 'Quote',       description: 'Literal source material' },
  { value: 'hypothesis', label: 'Speculation', description: 'Hypotheses, brainstorm' },
  { value: 'unknown',    label: 'Unknown',     description: 'Type not specified' },
]

const MARKDOWN_PROSE = 'overflow-y-auto max-h-64 text-xs text-gray-600 [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-gray-900 [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-gray-900 [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-gray-900 [&_h3]:mb-1 [&_p]:text-gray-600 [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-gray-600 [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-gray-600 [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-gray-900 [&_hr]:border-gray-200 [&_hr]:my-2'

// -- Source type catalogue (matches Superdock layout) ------------------------

const DIRECT_UPLOAD: SourceTypeOption[] = [
  { type: 'file',  label: m.add_source_type_file,  description: m.add_source_type_file_desc,  Icon: FileUp },
  { type: 'url',   label: m.add_source_type_url,   description: m.add_source_type_url_desc,   Icon: Globe },
  { type: 'text',  label: m.add_source_type_text,  description: m.add_source_type_text_desc,  Icon: Type },
  { type: 'image', label: m.add_source_type_image, description: m.add_source_type_image_desc, Icon: Image, comingSoon: true },
]

const WEBSITE_MEDIA: SourceTypeOption[] = [
  { type: 'web_crawler', label: m.add_source_type_website, description: m.add_source_type_website_desc, Icon: Globe },
  { type: 'youtube',     label: m.add_source_type_youtube, description: m.add_source_type_youtube_desc, Icon: SiYoutube, comingSoon: true },
  { type: 'rss',         label: m.add_source_type_rss,     description: m.add_source_type_rss_desc,     Icon: Rss,       comingSoon: true },
]

const GOOGLE: SourceTypeOption[] = [
  { type: 'google_drive',   label: m.add_source_type_google_drive,   description: m.add_source_type_google_drive_desc,   Icon: SiGoogledrive,  comingSoon: true },
  { type: 'gmail',          label: m.add_source_type_gmail,          description: m.add_source_type_gmail_desc,          Icon: SiGmail,        comingSoon: true },
  { type: 'google_sheets',  label: m.add_source_type_google_sheets,  description: m.add_source_type_google_sheets_desc,  Icon: SiGooglesheets, comingSoon: true },
]

const PRODUCTIVITY: SourceTypeOption[] = [
  { type: 'notion',      label: m.add_source_type_notion,      description: m.add_source_type_notion_desc,      Icon: SiNotion },
  { type: 'confluence',  label: m.add_source_type_confluence,  description: m.add_source_type_confluence_desc,  Icon: SiConfluence, comingSoon: true },
  { type: 'slack',       label: m.add_source_type_slack,       description: m.add_source_type_slack_desc,       Icon: MessageSquare, comingSoon: true },
  { type: 'airtable',    label: m.add_source_type_airtable,    description: m.add_source_type_airtable_desc,    Icon: SiAirtable,   comingSoon: true },
]

const DEVELOPMENT: SourceTypeOption[] = [
  { type: 'github', label: m.add_source_type_github, description: m.add_source_type_github_desc, Icon: SiGithub },
]

// -- Helpers -----------------------------------------------------------------

function stepLabel(current: number, total: number, label: string): string {
  return `Stap ${current} van ${total} \u2014 ${label}`
}

// -- Route -------------------------------------------------------------------

export const Route = createFileRoute('/app/knowledge/$kbSlug_/add-source')({
  component: AddSourcePage,
})

// -- Main component ----------------------------------------------------------

function AddSourcePage() {
  const { kbSlug } = Route.useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const token = user?.access_token
  const queryClient = useQueryClient()

  const [sourceType, setSourceType] = useState<SourceType | null>(null)

  // Shared connector state
  const [name, setName] = useState('')
  const [allowedAssertionModes, setAllowedAssertionModes] = useState<string[]>([])

  // GitHub
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '',
  })

  // Notion
  const [notionConfig, setNotionConfig] = useState<NotionConfig>({
    access_token: '', database_ids: '', max_pages: '500',
  })
  const [notionStep, setNotionStep] = useState<'credentials' | 'settings'>('credentials')

  // Web crawler
  const [webcrawlerConfig, setWebcrawlerConfig] = useState<WebCrawlerConfig>({
    base_url: '', path_prefix: '', max_pages: '200', content_selector: '',
  })
  const [wcStep, setWcStep] = useState<WcStep>('details')
  const [showAdvancedSelector, setShowAdvancedSelector] = useState(false)
  const [wcPreviewUrl, setWcPreviewUrl] = useState('')
  const [previewResult, setPreviewResult] = useState<{
    fit_markdown: string; word_count: number; warnings: string[]
    content_selector: string | null; selector_source: string | null
  } | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)

  // URL (single page)
  const [urlValue, setUrlValue] = useState('')

  // Text
  const [textContent, setTextContent] = useState('')

  // File
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [isDragOver, setIsDragOver] = useState(false)

  // Fetch KB name for the header
  const { data: kb } = useQuery({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: () => apiFetch<{ name: string }>(`/api/app/knowledge-bases/${kbSlug}`, token),
    enabled: !!token,
  })

  function goBack() {
    void navigate({ to: '/app/knowledge' })
  }

  function resetAndPickType(type: SourceType) {
    setSourceType(type)
    setName('')
    setAllowedAssertionModes([])
    setWcStep('details')
    setNotionStep('credentials')
    setShowAdvancedSelector(false)
    setPreviewResult(null)
    setWcPreviewUrl('')
    setPreviewError(null)
    setUrlValue('')
    setTextContent('')
    setSelectedFiles([])
  }

  // -- Connector create mutation ---
  const createConnectorMutation = useMutation({
    mutationFn: async () => {
      const config: Record<string, unknown> = {}
      let connectorType = sourceType

      if (sourceType === 'github') {
        config.installation_id = Number(githubConfig.installation_id)
        config.repo_owner = githubConfig.repo_owner
        config.repo_name = githubConfig.repo_name
        config.branch = githubConfig.branch
        if (githubConfig.path_filter) config.path_filter = githubConfig.path_filter
      }

      if (sourceType === 'web_crawler') {
        config.base_url = webcrawlerConfig.base_url
        if (webcrawlerConfig.path_prefix) config.path_prefix = webcrawlerConfig.path_prefix
        if (webcrawlerConfig.max_pages && webcrawlerConfig.max_pages !== '200')
          config.max_pages = Number(webcrawlerConfig.max_pages)
        if (webcrawlerConfig.content_selector) config.content_selector = webcrawlerConfig.content_selector
      }

      if (sourceType === 'url') {
        connectorType = 'web_crawler'
        config.base_url = urlValue
        config.max_pages = 1
      }

      if (sourceType === 'notion') {
        config.access_token = notionConfig.access_token
        const ids = notionConfig.database_ids.split('\n').map((s) => s.trim()).filter(Boolean)
        if (ids.length > 0) config.database_ids = ids
        if (notionConfig.max_pages && notionConfig.max_pages !== '500')
          config.max_pages = Number(notionConfig.max_pages)
      }

      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/`, token, {
        method: 'POST',
        body: JSON.stringify({
          name,
          connector_type: connectorType,
          config,
          schedule: null,
          allowed_assertion_modes: allowedAssertionModes.length > 0 ? allowedAssertionModes : null,
        }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      goBack()
    },
  })

  // -- Web crawler preview mutation ---
  const previewMutation = useMutation({
    mutationFn: async ({ url, content_selector, try_ai }: { url: string; content_selector?: string; try_ai?: boolean }) =>
      apiFetch<{
        fit_markdown: string; word_count: number; warnings: string[]; url: string
        content_selector: string | null; selector_source: string | null
      }>(`/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`, token, {
        method: 'POST',
        body: JSON.stringify({ url, content_selector: content_selector || null, try_ai: try_ai ?? false }),
      }),
    onSuccess: (data) => {
      setPreviewResult(data)
      setPreviewError(null)
      if (data.word_count === 0 || (data.warnings ?? []).length > 0) setShowAdvancedSelector(true)
    },
    onError: (err) => {
      setPreviewError(err instanceof Error ? err.message : 'Preview failed')
      setPreviewResult(null)
    },
  })

  // -- File drop handlers ---
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

  // -- Text ingest mutation ---
  const textIngestMutation = useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/documents/text`, token, {
        method: 'POST',
        body: JSON.stringify({ title: name, content: textContent, content_type: 'text/plain' }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      goBack()
    },
  })

  // -- File upload mutation ---
  const fileUploadMutation = useMutation({
    mutationFn: async () => {
      for (const file of selectedFiles) {
        const formData = new FormData()
        formData.append('file', file)
        await apiFetch(`/api/app/knowledge-bases/${kbSlug}/documents/upload`, token, {
          method: 'POST',
          body: formData,
        })
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      goBack()
    },
  })

  // Step counts for step labels
  const isSimpleConnector = sourceType === 'github'
  const wcTotalSteps = 4
  const notionTotalSteps = 2
  const githubTotalSteps = 2

  return (
    <div className="mx-auto max-w-3xl px-6 py-10" style={{ fontFamily: 'Inter, system-ui, sans-serif' }}>
      {/* Page header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">
            {m.add_source_title()}
          </h1>
          {kb && (
            <p className="text-sm text-gray-400 mt-0.5">
              {m.add_source_to_collection({ name: kb.name })}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={goBack}
          className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
        >
          <ArrowLeft className="h-4 w-4 inline mr-1" />
          {m.admin_connectors_cancel()}
        </button>
      </div>

      {/* ── Step 1: Source type grid ─────────────────────────────────── */}
      {!sourceType && (
        <div className="space-y-8">
          <SourceCategory title={m.add_source_category_direct()} types={DIRECT_UPLOAD} onSelect={resetAndPickType} />
          <SourceCategory title={m.add_source_category_website()} types={WEBSITE_MEDIA} onSelect={resetAndPickType} />
          <SourceCategory title={m.add_source_category_google()} types={GOOGLE} onSelect={resetAndPickType} />
          <SourceCategory title={m.add_source_category_productivity()} types={PRODUCTIVITY} onSelect={resetAndPickType} />
          <SourceCategory title={m.add_source_category_development()} types={DEVELOPMENT} onSelect={resetAndPickType} />
        </div>
      )}

      {/* ── File upload form ─────────────────────────────────────────── */}
      {sourceType === 'file' && (
        <div className="space-y-6">
          <p className="text-sm text-gray-400">Stap 2 van 2 &mdash; Upload bestanden</p>

          {/* Drop zone */}
          <div
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={() => setIsDragOver(false)}
            className={`rounded-lg border-2 border-dashed py-12 text-center transition-colors ${
              isDragOver ? 'border-gray-400 bg-gray-50' : 'border-gray-200'
            }`}
          >
            <Upload className="h-8 w-8 text-gray-300 mx-auto mb-3" />
            <p className="text-sm font-medium text-gray-900">
              Sleep bestanden hierheen
            </p>
            <p className="text-xs text-gray-400 mt-1">of</p>
            <label className="mt-3 inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors">
              <FileUp className="h-4 w-4" />
              Kies bestanden
              <input
                type="file"
                multiple
                accept=".pdf,.doc,.docx,.xls,.xlsx,.pptx,.txt,.md,.csv"
                className="sr-only"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? [])
                  if (files.length > 0) setSelectedFiles((prev) => [...prev, ...files])
                }}
              />
            </label>
            <p className="text-xs text-gray-400 mt-3">PDF, Word, Excel, PowerPoint, TXT, Markdown, CSV</p>
          </div>

          {/* Selected files */}
          {selectedFiles.length > 0 && (
            <div className="space-y-1">
              {selectedFiles.map((f, i) => (
                <div key={`${f.name}-${i}`} className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-2.5">
                  <FileText className="h-4 w-4 text-gray-400 shrink-0" />
                  <span className="text-sm text-gray-900 flex-1 truncate">{f.name}</span>
                  <span className="text-xs text-gray-400">{(f.size / 1024).toFixed(0)} KB</span>
                  <button
                    type="button"
                    onClick={() => setSelectedFiles((prev) => prev.filter((_, j) => j !== i))}
                    className="text-xs text-gray-400 hover:text-[var(--color-destructive)] transition-colors"
                  >
                    &times;
                  </button>
                </div>
              ))}
            </div>
          )}

          {fileUploadMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">
              {fileUploadMutation.error instanceof Error ? fileUploadMutation.error.message : m.admin_connectors_error_create_generic()}
            </p>
          )}
          <div className="flex items-center gap-3 pt-2">
            <Button
              type="button"
              size="sm"
              disabled={selectedFiles.length === 0 || fileUploadMutation.isPending}
              onClick={() => fileUploadMutation.mutate()}
              className="rounded-lg bg-gray-900 text-white hover:bg-gray-800"
            >
              {fileUploadMutation.isPending
                ? m.admin_connectors_create_submit_loading()
                : `Upload (${selectedFiles.length})`}
            </Button>
            <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
              {m.admin_connectors_webcrawler_back()}
            </button>
          </div>
        </div>
      )}

      {/* ── URL form (single page scrape) ────────────────────────────── */}
      {sourceType === 'url' && (
        <div className="space-y-6">
          <p className="text-sm text-gray-400">Stap 2 van 2 &mdash; Webpagina toevoegen</p>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createConnectorMutation.mutate()
            }}
            className="space-y-4"
          >
            <div className="space-y-1.5">
              <Label htmlFor="url-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input
                id="url-name"
                required
                placeholder="Bijv. Product documentatie"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-lg border border-gray-200 text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="url-value" className="text-sm font-medium text-gray-900">URL</Label>
              <Input
                id="url-value"
                type="url"
                required
                placeholder="https://example.com/page"
                value={urlValue}
                onChange={(e) => setUrlValue(e.target.value)}
                className="rounded-lg border border-gray-200 text-sm"
              />
            </div>
            {createConnectorMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {createConnectorMutation.error instanceof Error ? createConnectorMutation.error.message : m.admin_connectors_error_create_generic()}
              </p>
            )}
            <div className="flex items-center gap-3 pt-2">
              <Button type="submit" size="sm" disabled={createConnectorMutation.isPending} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">
                {createConnectorMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
              </Button>
              <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                {m.admin_connectors_webcrawler_back()}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Text form ────────────────────────────────────────────────── */}
      {sourceType === 'text' && (
        <div className="space-y-6">
          <p className="text-sm text-gray-400">Stap 2 van 2 &mdash; Tekst toevoegen</p>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="text-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input
                id="text-name"
                required
                placeholder="Bijv. Product FAQ"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-lg border border-gray-200 text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="text-content" className="text-sm font-medium text-gray-900">Content</Label>
              <textarea
                id="text-content"
                className="flex min-h-[200px] w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400"
                placeholder="Plak of typ je tekst hier..."
                value={textContent}
                onChange={(e) => setTextContent(e.target.value)}
              />
              {textContent && (
                <p className="text-xs text-gray-400">{textContent.split(/\s+/).filter(Boolean).length} woorden</p>
              )}
            </div>
            {textIngestMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {textIngestMutation.error instanceof Error ? textIngestMutation.error.message : m.admin_connectors_error_create_generic()}
              </p>
            )}
            <div className="flex items-center gap-3 pt-2">
              <Button
                type="button"
                size="sm"
                disabled={!name || !textContent || textIngestMutation.isPending}
                onClick={() => textIngestMutation.mutate()}
                className="rounded-lg bg-gray-900 text-white hover:bg-gray-800"
              >
                {textIngestMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
              </Button>
              <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                {m.admin_connectors_webcrawler_back()}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── GitHub form ──────────────────────────────────────────────── */}
      {sourceType === 'github' && (
        <div className="space-y-6">
          <p className="text-sm text-gray-400">{stepLabel(2, githubTotalSteps, m.admin_connectors_step_configure())}</p>
          <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="conn-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input id="conn-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="conn-install" className="text-sm font-medium text-gray-900">{m.admin_connectors_github_installation_id()}</Label>
              <Input id="conn-install" type="number" required value={githubConfig.installation_id} onChange={(e) => setGithubConfig((p) => ({ ...p, installation_id: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="conn-owner" className="text-sm font-medium text-gray-900">{m.admin_connectors_github_repo_owner()}</Label>
                <Input id="conn-owner" required value={githubConfig.repo_owner} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_owner: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="conn-repo" className="text-sm font-medium text-gray-900">{m.admin_connectors_github_repo_name()}</Label>
                <Input id="conn-repo" required value={githubConfig.repo_name} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_name: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="conn-branch" className="text-sm font-medium text-gray-900">{m.admin_connectors_github_branch()}</Label>
              <Input id="conn-branch" required placeholder={m.admin_connectors_github_branch_placeholder()} value={githubConfig.branch} onChange={(e) => setGithubConfig((p) => ({ ...p, branch: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm font-medium text-gray-900">{m.admin_connectors_assertion_modes_label()}</Label>
              <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
            </div>
            {createConnectorMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {createConnectorMutation.error instanceof Error ? createConnectorMutation.error.message : m.admin_connectors_error_create_generic()}
              </p>
            )}
            <div className="flex items-center gap-3 pt-2">
              <Button type="submit" size="sm" disabled={createConnectorMutation.isPending} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">
                {createConnectorMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
              </Button>
              <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                {m.admin_connectors_webcrawler_back()}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Notion form ──────────────────────────────────────────────── */}
      {sourceType === 'notion' && (
        <div className="space-y-6">
          {notionStep === 'credentials' && (
            <>
              <p className="text-sm text-gray-400">{stepLabel(2, notionTotalSteps, m.admin_connectors_step_configure())}</p>
              <form onSubmit={(e) => { e.preventDefault(); setNotionStep('settings') }} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="notion-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
                  <Input id="notion-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="notion-token" className="text-sm font-medium text-gray-900">{m.admin_connectors_notion_access_token()}</Label>
                  <Input id="notion-token" type="password" required placeholder={m.admin_connectors_notion_access_token_placeholder()} value={notionConfig.access_token} onChange={(e) => setNotionConfig((p) => ({ ...p, access_token: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
                  <p className="text-xs text-gray-400">
                    {m.admin_connectors_notion_token_help_prefix()}{' '}
                    <a href="https://www.notion.so/my-integrations" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-gray-900 hover:text-gray-600 underline underline-offset-2">
                      notion.so/my-integrations
                    </a>{' '}
                    {m.admin_connectors_notion_token_help_suffix()}
                  </p>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="notion-db-ids" className="text-sm font-medium text-gray-900">{m.admin_connectors_notion_database_ids()}</Label>
                  <textarea
                    id="notion-db-ids"
                    className="flex min-h-[80px] w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400"
                    placeholder={m.admin_connectors_notion_database_ids_placeholder()}
                    value={notionConfig.database_ids}
                    onChange={(e) => setNotionConfig((p) => ({ ...p, database_ids: e.target.value }))}
                  />
                </div>
                <div className="flex items-center gap-3 pt-2">
                  <Button type="submit" size="sm" disabled={!name || !notionConfig.access_token} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">
                    {m.admin_connectors_webcrawler_next()}
                  </Button>
                  <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                    {m.admin_connectors_webcrawler_back()}
                  </button>
                </div>
              </form>
            </>
          )}
          {notionStep === 'settings' && (
            <>
              <p className="text-sm text-gray-400">{stepLabel(2, notionTotalSteps, m.admin_connectors_step_configure())}</p>
              <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-sm font-medium text-gray-900">{m.admin_connectors_assertion_modes_label()}</Label>
                  <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="notion-max-pages" className="text-sm font-medium text-gray-900">{m.admin_connectors_notion_max_pages()}</Label>
                  <Input id="notion-max-pages" type="number" min="1" max="2000" value={notionConfig.max_pages} onChange={(e) => setNotionConfig((p) => ({ ...p, max_pages: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
                </div>
                {createConnectorMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createConnectorMutation.error instanceof Error ? createConnectorMutation.error.message : m.admin_connectors_error_create_generic()}
                  </p>
                )}
                <div className="flex items-center gap-3 pt-2">
                  <Button type="submit" size="sm" disabled={createConnectorMutation.isPending} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">
                    {createConnectorMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                  </Button>
                  <button type="button" onClick={() => setNotionStep('credentials')} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                    {m.admin_connectors_webcrawler_back()}
                  </button>
                </div>
              </form>
            </>
          )}
        </div>
      )}

      {/* ── Web crawler wizard ───────────────────────────────────────── */}
      {sourceType === 'web_crawler' && (
        <div className="space-y-6">
          {/* Step 1: Details */}
          {wcStep === 'details' && (
            <>
              <p className="text-sm text-gray-400">{stepLabel(2, wcTotalSteps, m.admin_connectors_webcrawler_step_details())}</p>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="wc-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
                  <Input id="wc-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="wc-base-url" className="text-sm font-medium text-gray-900">{m.admin_connectors_webcrawler_base_url()}</Label>
                  <Input id="wc-base-url" type="url" required placeholder={m.admin_connectors_webcrawler_base_url_placeholder()} value={webcrawlerConfig.base_url} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, base_url: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="wc-path-prefix" className="text-sm font-medium text-gray-900">{m.admin_connectors_webcrawler_path_prefix()}</Label>
                  <Input id="wc-path-prefix" placeholder={m.admin_connectors_webcrawler_path_prefix_placeholder()} value={webcrawlerConfig.path_prefix} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, path_prefix: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
                </div>
                <div className="flex items-center gap-3 pt-2">
                  <Button
                    type="button" size="sm"
                    disabled={!name || !webcrawlerConfig.base_url}
                    onClick={() => { setWcPreviewUrl(webcrawlerConfig.base_url); setPreviewResult(null); setPreviewError(null); setWcStep('preview') }}
                    className="rounded-lg bg-gray-900 text-white hover:bg-gray-800"
                  >
                    {m.admin_connectors_webcrawler_next()}
                  </Button>
                  <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                    {m.admin_connectors_webcrawler_back()}
                  </button>
                </div>
              </div>
            </>
          )}

          {/* Step 2: Preview */}
          {wcStep === 'preview' && (
            <>
              <p className="text-sm text-gray-400">{stepLabel(3, wcTotalSteps, m.admin_connectors_webcrawler_step_preview())}</p>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="wc-preview-url" className="text-sm font-medium text-gray-900">{m.admin_connectors_webcrawler_preview_url()}</Label>
                  <Input id="wc-preview-url" type="url" placeholder={webcrawlerConfig.base_url} value={wcPreviewUrl} onChange={(e) => setWcPreviewUrl(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
                </div>
                <button type="button" className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors" onClick={() => setShowAdvancedSelector((p) => !p)}>
                  <Settings className="h-3 w-3" />
                  {m.admin_connectors_webcrawler_advanced_toggle()}
                  {showAdvancedSelector ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                </button>
                {showAdvancedSelector && (
                  <div className="pl-4 border-l-2 border-gray-200">
                    <Input placeholder={m.admin_connectors_webcrawler_content_selector_placeholder()} value={webcrawlerConfig.content_selector} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, content_selector: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
                  </div>
                )}
                {!webcrawlerConfig.content_selector && (
                  <button
                    type="button"
                    className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors disabled:opacity-50"
                    disabled={previewMutation.isPending || !wcPreviewUrl}
                    onClick={() => { setPreviewResult(null); setPreviewError(null); previewMutation.mutate({ url: wcPreviewUrl, try_ai: true }) }}
                  >
                    <Sparkles className="h-3 w-3" />
                    {m.admin_connectors_webcrawler_try_ai()}
                  </button>
                )}
                <Button
                  type="button" size="sm" variant="outline"
                  disabled={previewMutation.isPending || !wcPreviewUrl}
                  onClick={() => { setPreviewResult(null); setPreviewError(null); previewMutation.mutate({ url: wcPreviewUrl, content_selector: webcrawlerConfig.content_selector }) }}
                  className="rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50"
                >
                  {previewMutation.isPending
                    ? <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />{m.admin_connectors_webcrawler_preview_loading()}</>
                    : m.admin_connectors_webcrawler_run_preview()}
                </Button>

                {/* Preview feedback */}
                {previewError && !previewMutation.isPending && (
                  <p className="text-sm text-[var(--color-destructive)]">{previewError}</p>
                )}
                {previewMutation.isPending && (
                  <div className="rounded-lg border border-gray-200 p-4 flex items-center gap-2 text-sm text-gray-400">
                    <Loader2 className="h-4 w-4 animate-spin" /> {m.admin_connectors_webcrawler_preview_loading()}
                  </div>
                )}
                {previewResult !== null && !previewMutation.isPending && (previewResult.warnings ?? []).length === 0 && previewResult.word_count > 0 && (
                  <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 text-xs text-[var(--color-success)]">
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                    <span>{m.admin_connectors_webcrawler_preview_looks_good({ count: String(previewResult.word_count) })}</span>
                  </div>
                )}
                {previewResult !== null && !previewMutation.isPending && (previewResult.warnings ?? []).length > 0 && (
                  <div className="flex gap-2 items-start rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 p-3 text-xs text-[var(--color-warning)]">
                    <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    <span>{m.admin_connectors_webcrawler_warning_nav_detected()}</span>
                  </div>
                )}
                {previewResult !== null && !previewMutation.isPending && previewResult.word_count === 0 && previewResult.selector_source !== 'ai' && (
                  <div className="space-y-2">
                    <div className="flex gap-2 items-start rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 p-3 text-xs text-[var(--color-warning)]">
                      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                      <span>{m.admin_connectors_webcrawler_preview_no_content()}</span>
                    </div>
                    {!webcrawlerConfig.content_selector && (
                      <button type="button" className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors"
                        onClick={() => { setPreviewResult(null); setPreviewError(null); previewMutation.mutate({ url: wcPreviewUrl, try_ai: true }) }}>
                        <Sparkles className="h-3 w-3" /> {m.admin_connectors_webcrawler_try_ai()}
                      </button>
                    )}
                  </div>
                )}
                {previewResult !== null && !previewMutation.isPending && previewResult.selector_source === 'ai' && previewResult.content_selector && (
                  <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-2">
                    <div className="flex gap-2 items-center text-xs text-gray-600">
                      <Sparkles className="h-3.5 w-3.5 shrink-0" />
                      <span>{m.admin_connectors_webcrawler_ai_selector_detected({ selector: previewResult.content_selector, count: String(previewResult.word_count) })}</span>
                    </div>
                    {webcrawlerConfig.content_selector !== previewResult.content_selector && (
                      <Button type="button" size="sm" variant="outline" className="text-xs h-7 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50"
                        onClick={() => { setWebcrawlerConfig((p) => ({ ...p, content_selector: previewResult.content_selector! })); setShowAdvancedSelector(true) }}>
                        {m.admin_connectors_webcrawler_ai_selector_use()}
                      </Button>
                    )}
                  </div>
                )}
                {!previewResult && !previewMutation.isPending && (
                  <p className="text-sm text-gray-400">{m.admin_connectors_webcrawler_preview_empty()}</p>
                )}
                {previewResult !== null && !previewMutation.isPending && previewResult.word_count > 0 && (
                  <div className="rounded-lg border border-gray-200 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-900">{m.admin_connectors_webcrawler_preview_title()}</span>
                      <span className="text-xs text-gray-400">{m.admin_connectors_webcrawler_preview_word_count({ count: String(previewResult.word_count) })}</span>
                    </div>
                    {previewResult.fit_markdown.trim() ? (
                      <div className={MARKDOWN_PROSE}>
                        <ReactMarkdown components={{ a: ({ children }) => <span className="text-gray-900">{children}</span> }}>{previewResult.fit_markdown}</ReactMarkdown>
                      </div>
                    ) : (
                      <p className="text-sm text-gray-400">{m.admin_connectors_webcrawler_preview_empty()}</p>
                    )}
                  </div>
                )}

                <div className="flex items-center gap-3 pt-2">
                  <Button type="button" size="sm" onClick={() => setWcStep('settings')} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">{m.admin_connectors_webcrawler_next()}</Button>
                  <button type="button" onClick={() => setWcStep('details')} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">{m.admin_connectors_webcrawler_back()}</button>
                </div>
              </div>
            </>
          )}

          {/* Step 3: Settings */}
          {wcStep === 'settings' && (
            <>
              <p className="text-sm text-gray-400">{stepLabel(4, wcTotalSteps, m.admin_connectors_webcrawler_step_settings())}</p>
              <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-sm font-medium text-gray-900">{m.admin_connectors_assertion_modes_label()}</Label>
                  <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="wc-max-pages" className="text-sm font-medium text-gray-900">{m.admin_connectors_webcrawler_max_pages()}</Label>
                  <Input id="wc-max-pages" type="number" min="1" max="2000" placeholder={m.admin_connectors_webcrawler_max_pages_placeholder()} value={webcrawlerConfig.max_pages} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, max_pages: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
                </div>
                {createConnectorMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createConnectorMutation.error instanceof Error ? createConnectorMutation.error.message : m.admin_connectors_error_create_generic()}
                  </p>
                )}
                <div className="flex items-center gap-3 pt-2">
                  <Button type="submit" size="sm" disabled={createConnectorMutation.isPending} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">
                    {createConnectorMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                  </Button>
                  <button type="button" onClick={() => setWcStep('preview')} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">{m.admin_connectors_webcrawler_back()}</button>
                </div>
              </form>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/* ── Source type grid ─────────────────────────────────────────────── */

function SourceCategory({
  title,
  types,
  onSelect,
}: {
  title: string
  types: SourceTypeOption[]
  onSelect: (type: SourceType) => void
}) {
  return (
    <div>
      <h3 className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-3">
        {title}
      </h3>
      <div className="grid grid-cols-2 gap-3">
        {types.map(({ type, label, description, Icon, comingSoon }) =>
          comingSoon ? (
            <div
              key={type}
              className="flex items-start gap-3 rounded-lg border border-gray-200 p-4 opacity-50 cursor-not-allowed"
            >
              <Icon className="h-5 w-5 text-gray-400 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <span className="text-sm font-medium text-gray-400 block">{label()}</span>
                <span className="text-xs text-gray-300 mt-0.5 block">{description()}</span>
              </div>
              <Badge variant="outline" className="text-[10px] shrink-0">{m.add_source_coming_soon()}</Badge>
            </div>
          ) : (
            <button
              key={type}
              type="button"
              onClick={() => onSelect(type)}
              className="flex items-start gap-3 rounded-lg border border-gray-200 p-4 text-left transition-all hover:border-gray-400 hover:shadow-sm"
            >
              <Icon className="h-5 w-5 text-gray-900 mt-0.5 shrink-0" />
              <div>
                <span className="text-sm font-medium text-gray-900 block">{label()}</span>
                <span className="text-xs text-gray-400 mt-0.5 block">{description()}</span>
              </div>
            </button>
          ),
        )}
      </div>
    </div>
  )
}
