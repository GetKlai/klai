import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, ChevronRight, ChevronDown, AlertTriangle, CheckCircle2,
  Loader2, Sparkles, Globe, FileText, FileUp, Type, Settings, Upload,
  Image, Rss, MessageSquare,
} from 'lucide-react'
import {
  SiGithub, SiNotion, SiGoogledrive, SiYoutube, SiGmail,
  SiGooglesheets, SiConfluence, SiAirtable,
} from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
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
  { type: 'image', label: m.add_source_type_image, description: m.add_source_type_image_desc, Icon: Image },
]

const WEBSITE_MEDIA: SourceTypeOption[] = [
  { type: 'web_crawler', label: m.add_source_type_website, description: m.add_source_type_website_desc, Icon: Globe },
  { type: 'youtube',     label: m.add_source_type_youtube, description: m.add_source_type_youtube_desc, Icon: SiYoutube },
  { type: 'rss',         label: m.add_source_type_rss,     description: m.add_source_type_rss_desc,     Icon: Rss },
]

const GOOGLE: SourceTypeOption[] = [
  { type: 'google_drive',   label: m.add_source_type_google_drive,   description: m.add_source_type_google_drive_desc,   Icon: SiGoogledrive },
  { type: 'gmail',          label: m.add_source_type_gmail,          description: m.add_source_type_gmail_desc,          Icon: SiGmail },
  { type: 'google_sheets',  label: m.add_source_type_google_sheets,  description: m.add_source_type_google_sheets_desc,  Icon: SiGooglesheets },
]

const PRODUCTIVITY: SourceTypeOption[] = [
  { type: 'notion',      label: m.add_source_type_notion,      description: m.add_source_type_notion_desc,      Icon: SiNotion },
  { type: 'confluence',  label: m.add_source_type_confluence,  description: m.add_source_type_confluence_desc,  Icon: SiConfluence },
  { type: 'slack',       label: m.add_source_type_slack,       description: m.add_source_type_slack_desc,       Icon: MessageSquare },
  { type: 'airtable',    label: m.add_source_type_airtable,    description: m.add_source_type_airtable_desc,    Icon: SiAirtable },
]

const DEVELOPMENT: SourceTypeOption[] = [
  { type: 'github', label: m.add_source_type_github, description: m.add_source_type_github_desc, Icon: SiGithub },
]


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

  // Image (same as file but image-only)
  const [selectedImages, setSelectedImages] = useState<File[]>([])

  // YouTube
  const [youtubeUrl, setYoutubeUrl] = useState('')

  // RSS
  const [rssUrl, setRssUrl] = useState('')
  const [rssMaxItems, setRssMaxItems] = useState('50')

  // Confluence
  const [confluenceConfig, setConfluenceConfig] = useState({
    base_url: '', email: '', api_token: '', space_keys: '',
  })

  // Slack
  const [slackConfig, setSlackConfig] = useState({
    bot_token: '', channel_ids: '',
  })

  // Airtable
  const [airtableConfig, setAirtableConfig] = useState({
    api_key: '', base_id: '', table_names: '',
  })

  // Google Drive
  const [googleDriveConfig, setGoogleDriveConfig] = useState({
    service_account_json: '', folder_ids: '',
  })

  // Gmail
  const [gmailConfig, setGmailConfig] = useState({
    service_account_json: '', user_email: '', query: '',
  })

  // Google Sheets
  const [googleSheetsConfig, setGoogleSheetsConfig] = useState({
    service_account_json: '', spreadsheet_ids: '',
  })

  // Fetch all KBs for collection switcher
  const { data: allKbsData } = useQuery({
    queryKey: ['app-knowledge-bases'],
    queryFn: () => apiFetch<{ knowledge_bases: { id: number; name: string; slug: string }[] }>('/api/app/knowledge-bases', token),
    enabled: !!token,
  })
  const allKbs = allKbsData?.knowledge_bases ?? []

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
    setSelectedImages([])
    setYoutubeUrl('')
    setRssUrl('')
    setRssMaxItems('50')
    setConfluenceConfig({ base_url: '', email: '', api_token: '', space_keys: '' })
    setSlackConfig({ bot_token: '', channel_ids: '' })
    setAirtableConfig({ api_key: '', base_id: '', table_names: '' })
    setGoogleDriveConfig({ service_account_json: '', folder_ids: '' })
    setGmailConfig({ service_account_json: '', user_email: '', query: '' })
    setGoogleSheetsConfig({ service_account_json: '', spreadsheet_ids: '' })
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

      if (sourceType === 'confluence') {
        config.base_url = confluenceConfig.base_url
        config.email = confluenceConfig.email
        config.api_token = confluenceConfig.api_token
        const keys = confluenceConfig.space_keys.split(',').map((s) => s.trim()).filter(Boolean)
        if (keys.length > 0) config.space_keys = keys
      }

      if (sourceType === 'slack') {
        config.bot_token = slackConfig.bot_token
        const ids = slackConfig.channel_ids.split(',').map((s) => s.trim()).filter(Boolean)
        if (ids.length > 0) config.channel_ids = ids
      }

      if (sourceType === 'airtable') {
        config.api_key = airtableConfig.api_key
        config.base_id = airtableConfig.base_id
        const tables = airtableConfig.table_names.split(',').map((s) => s.trim()).filter(Boolean)
        if (tables.length > 0) config.table_names = tables
      }

      if (sourceType === 'google_drive') {
        config.service_account_json = googleDriveConfig.service_account_json
        const ids = googleDriveConfig.folder_ids.split(',').map((s) => s.trim()).filter(Boolean)
        if (ids.length > 0) config.folder_ids = ids
      }

      if (sourceType === 'gmail') {
        config.service_account_json = gmailConfig.service_account_json
        config.user_email = gmailConfig.user_email
        config.query = gmailConfig.query
      }

      if (sourceType === 'google_sheets') {
        config.service_account_json = googleSheetsConfig.service_account_json
        const ids = googleSheetsConfig.spreadsheet_ids.split(',').map((s) => s.trim()).filter(Boolean)
        if (ids.length > 0) config.spreadsheet_ids = ids
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

  // -- YouTube ingest mutation ---
  const youtubeIngestMutation = useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/documents/youtube`, token, {
        method: 'POST',
        body: JSON.stringify({ url: youtubeUrl, title: name || undefined }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      goBack()
    },
  })

  // -- RSS ingest mutation ---
  const rssIngestMutation = useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/documents/rss`, token, {
        method: 'POST',
        body: JSON.stringify({ url: rssUrl, title: name || undefined, max_items: Number(rssMaxItems) }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      goBack()
    },
  })

  // -- Image upload mutation ---
  const imageUploadMutation = useMutation({
    mutationFn: async () => {
      for (const file of selectedImages) {
        const formData = new FormData()
        formData.append('file', file)
        await apiFetch(`/api/app/knowledge-bases/${kbSlug}/documents/upload-image`, token, {
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

  // -- Step indicator logic --
  const wizardStep: 'collection' | 'type' | 'configure' =
    !sourceType ? 'type' : 'configure'

  const steps: StepItem[] = [
    { label: 'Collectie', onClick: () => { /* already chosen via URL */ } },
    { label: 'Brontype', onClick: () => setSourceType(null) },
    { label: 'Configureren' },
  ]
  const stepIndex = wizardStep === 'type' ? 1 : 2

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.add_source_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_cancel()}
        </Button>
      </div>

      {/* Step indicator */}
      <StepIndicator steps={steps} currentIndex={stepIndex} />

      <div className="mt-6">

      {/* ── Step 1 (already done): Collection selector ─────────────── */}
      {/* Shown as a compact bar so user can switch collection at any point */}
      <div className="flex items-center gap-2 mb-6 px-1">
        <span className="text-sm text-[var(--color-muted-foreground)]">Collectie:</span>
        <select
          value={kbSlug}
          onChange={(e) => {
            void navigate({
              to: '/app/knowledge/$kbSlug/add-source',
              params: { kbSlug: e.target.value },
            })
          }}
          className="rounded-lg border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm font-semibold text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] cursor-pointer"
        >
          {allKbs.map((k) => (
            <option key={k.id} value={k.slug}>{k.name}</option>
          ))}
        </select>
      </div>

      {/* ── Step 2: Source type grid ────────────────────────────────── */}
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

      {/* ── Image upload form ──────────────────────────────────────── */}
      {sourceType === 'image' && (
        <div className="space-y-6">

          {/* Drop zone */}
          <div
            onDrop={(e) => {
              e.preventDefault()
              setIsDragOver(false)
              const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('image/'))
              if (files.length > 0) setSelectedImages((prev) => [...prev, ...files])
            }}
            onDragOver={(e) => { e.preventDefault(); setIsDragOver(true) }}
            onDragLeave={() => setIsDragOver(false)}
            className={`rounded-lg border-2 border-dashed py-12 text-center transition-colors ${
              isDragOver ? 'border-gray-400 bg-gray-50' : 'border-gray-200'
            }`}
          >
            <Image className="h-8 w-8 text-gray-300 mx-auto mb-3" />
            <p className="text-sm font-medium text-gray-900">Sleep afbeeldingen hierheen</p>
            <p className="text-xs text-gray-400 mt-1">of</p>
            <label className="mt-3 inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors">
              <Upload className="h-4 w-4" />
              Kies afbeeldingen
              <input
                type="file"
                multiple
                accept="image/png,image/jpeg,image/gif,image/webp"
                className="sr-only"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? [])
                  if (files.length > 0) setSelectedImages((prev) => [...prev, ...files])
                }}
              />
            </label>
            <p className="text-xs text-gray-400 mt-3">PNG, JPEG, GIF, WebP</p>
          </div>

          {/* Selected images */}
          {selectedImages.length > 0 && (
            <div className="space-y-1">
              {selectedImages.map((f, i) => (
                <div key={`${f.name}-${i}`} className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-2.5">
                  <Image className="h-4 w-4 text-gray-400 shrink-0" />
                  <span className="text-sm text-gray-900 flex-1 truncate">{f.name}</span>
                  <span className="text-xs text-gray-400">{(f.size / 1024).toFixed(0)} KB</span>
                  <button
                    type="button"
                    onClick={() => setSelectedImages((prev) => prev.filter((_, j) => j !== i))}
                    className="text-xs text-gray-400 hover:text-[var(--color-destructive)] transition-colors"
                  >
                    &times;
                  </button>
                </div>
              ))}
            </div>
          )}

          {imageUploadMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">
              {imageUploadMutation.error instanceof Error ? imageUploadMutation.error.message : m.admin_connectors_error_create_generic()}
            </p>
          )}
          <div className="flex items-center gap-3 pt-2">
            <Button
              type="button"
              size="sm"
              disabled={selectedImages.length === 0 || imageUploadMutation.isPending}
              onClick={() => imageUploadMutation.mutate()}
              className="rounded-lg bg-gray-900 text-white hover:bg-gray-800"
            >
              {imageUploadMutation.isPending
                ? m.admin_connectors_create_submit_loading()
                : `Upload (${selectedImages.length})`}
            </Button>
            <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
              {m.admin_connectors_webcrawler_back()}
            </button>
          </div>
        </div>
      )}

      {/* ── YouTube form ─────────────────────────────────────────────── */}
      {sourceType === 'youtube' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); youtubeIngestMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="yt-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()} (optioneel)</Label>
              <Input id="yt-name" placeholder="Bijv. Product demo" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="yt-url" className="text-sm font-medium text-gray-900">YouTube URL</Label>
              <Input id="yt-url" type="url" required placeholder="https://youtube.com/watch?v=..." value={youtubeUrl} onChange={(e) => setYoutubeUrl(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            {youtubeIngestMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {youtubeIngestMutation.error instanceof Error ? youtubeIngestMutation.error.message : m.admin_connectors_error_create_generic()}
              </p>
            )}
            <div className="flex items-center gap-3 pt-2">
              <Button type="submit" size="sm" disabled={youtubeIngestMutation.isPending} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">
                {youtubeIngestMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
              </Button>
              <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                {m.admin_connectors_webcrawler_back()}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── RSS form ─────────────────────────────────────────────────── */}
      {sourceType === 'rss' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); rssIngestMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="rss-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()} (optioneel)</Label>
              <Input id="rss-name" placeholder="Bijv. Tech blog" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rss-url" className="text-sm font-medium text-gray-900">Feed URL</Label>
              <Input id="rss-url" type="url" required placeholder="https://example.com/feed.xml" value={rssUrl} onChange={(e) => setRssUrl(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rss-max" className="text-sm font-medium text-gray-900">Max items</Label>
              <Input id="rss-max" type="number" min="1" max="200" value={rssMaxItems} onChange={(e) => setRssMaxItems(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            {rssIngestMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {rssIngestMutation.error instanceof Error ? rssIngestMutation.error.message : m.admin_connectors_error_create_generic()}
              </p>
            )}
            <div className="flex items-center gap-3 pt-2">
              <Button type="submit" size="sm" disabled={rssIngestMutation.isPending} className="rounded-lg bg-gray-900 text-white hover:bg-gray-800">
                {rssIngestMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
              </Button>
              <button type="button" onClick={() => setSourceType(null)} className="text-sm text-gray-400 hover:text-gray-900 transition-colors">
                {m.admin_connectors_webcrawler_back()}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Confluence form ──────────────────────────────────────────── */}
      {sourceType === 'confluence' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="conf-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input id="conf-name" required placeholder="Bijv. Engineering wiki" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="conf-url" className="text-sm font-medium text-gray-900">Confluence URL</Label>
              <Input id="conf-url" type="url" required placeholder="https://company.atlassian.net" value={confluenceConfig.base_url} onChange={(e) => setConfluenceConfig((p) => ({ ...p, base_url: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="conf-email" className="text-sm font-medium text-gray-900">E-mail</Label>
              <Input id="conf-email" type="email" required placeholder="user@company.com" value={confluenceConfig.email} onChange={(e) => setConfluenceConfig((p) => ({ ...p, email: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="conf-token" className="text-sm font-medium text-gray-900">API Token</Label>
              <Input id="conf-token" type="password" required placeholder="Atlassian API token" value={confluenceConfig.api_token} onChange={(e) => setConfluenceConfig((p) => ({ ...p, api_token: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="conf-spaces" className="text-sm font-medium text-gray-900">Space keys (optioneel, komma-gescheiden)</Label>
              <Input id="conf-spaces" placeholder="ENG, PROD, HR" value={confluenceConfig.space_keys} onChange={(e) => setConfluenceConfig((p) => ({ ...p, space_keys: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
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

      {/* ── Slack form ───────────────────────────────────────────────── */}
      {sourceType === 'slack' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="slack-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input id="slack-name" required placeholder="Bijv. Team kanalen" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="slack-token" className="text-sm font-medium text-gray-900">Bot Token</Label>
              <Input id="slack-token" type="password" required placeholder="xoxb-..." value={slackConfig.bot_token} onChange={(e) => setSlackConfig((p) => ({ ...p, bot_token: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="slack-channels" className="text-sm font-medium text-gray-900">Channel IDs (optioneel, komma-gescheiden)</Label>
              <Input id="slack-channels" placeholder="C01ABC123, C02DEF456" value={slackConfig.channel_ids} onChange={(e) => setSlackConfig((p) => ({ ...p, channel_ids: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
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

      {/* ── Airtable form ────────────────────────────────────────────── */}
      {sourceType === 'airtable' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="at-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input id="at-name" required placeholder="Bijv. Product database" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="at-key" className="text-sm font-medium text-gray-900">API Key</Label>
              <Input id="at-key" type="password" required placeholder="pat..." value={airtableConfig.api_key} onChange={(e) => setAirtableConfig((p) => ({ ...p, api_key: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="at-base" className="text-sm font-medium text-gray-900">Base ID</Label>
              <Input id="at-base" required placeholder="app..." value={airtableConfig.base_id} onChange={(e) => setAirtableConfig((p) => ({ ...p, base_id: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="at-tables" className="text-sm font-medium text-gray-900">Table names (optioneel, komma-gescheiden)</Label>
              <Input id="at-tables" placeholder="Products, Contacts" value={airtableConfig.table_names} onChange={(e) => setAirtableConfig((p) => ({ ...p, table_names: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
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

      {/* ── Google Drive form ────────────────────────────────────────── */}
      {sourceType === 'google_drive' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="gd-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input id="gd-name" required placeholder="Bijv. Gedeelde documenten" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gd-sa" className="text-sm font-medium text-gray-900">Service Account JSON</Label>
              <textarea
                id="gd-sa"
                required
                className="flex min-h-[120px] w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 font-mono placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400"
                placeholder='{"type": "service_account", ...}'
                value={googleDriveConfig.service_account_json}
                onChange={(e) => setGoogleDriveConfig((p) => ({ ...p, service_account_json: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gd-folders" className="text-sm font-medium text-gray-900">Folder IDs (optioneel, komma-gescheiden)</Label>
              <Input id="gd-folders" placeholder="1abc..., 2def..." value={googleDriveConfig.folder_ids} onChange={(e) => setGoogleDriveConfig((p) => ({ ...p, folder_ids: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
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

      {/* ── Gmail form ───────────────────────────────────────────────── */}
      {sourceType === 'gmail' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="gm-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input id="gm-name" required placeholder="Bijv. Support inbox" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gm-sa" className="text-sm font-medium text-gray-900">Service Account JSON</Label>
              <textarea
                id="gm-sa"
                required
                className="flex min-h-[120px] w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 font-mono placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400"
                placeholder='{"type": "service_account", ...}'
                value={gmailConfig.service_account_json}
                onChange={(e) => setGmailConfig((p) => ({ ...p, service_account_json: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gm-email" className="text-sm font-medium text-gray-900">User e-mail</Label>
              <Input id="gm-email" type="email" required placeholder="user@company.com" value={gmailConfig.user_email} onChange={(e) => setGmailConfig((p) => ({ ...p, user_email: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gm-query" className="text-sm font-medium text-gray-900">Zoekquery (optioneel)</Label>
              <Input id="gm-query" placeholder="label:important after:2024/01/01" value={gmailConfig.query} onChange={(e) => setGmailConfig((p) => ({ ...p, query: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
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

      {/* ── Google Sheets form ───────────────────────────────────────── */}
      {sourceType === 'google_sheets' && (
        <div className="space-y-6">
          <form onSubmit={(e) => { e.preventDefault(); createConnectorMutation.mutate() }} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="gs-name" className="text-sm font-medium text-gray-900">{m.admin_connectors_field_name()}</Label>
              <Input id="gs-name" required placeholder="Bijv. Product spreadsheet" value={name} onChange={(e) => setName(e.target.value)} className="rounded-lg border border-gray-200 text-sm" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gs-sa" className="text-sm font-medium text-gray-900">Service Account JSON</Label>
              <textarea
                id="gs-sa"
                required
                className="flex min-h-[120px] w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 font-mono placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400"
                placeholder='{"type": "service_account", ...}'
                value={googleSheetsConfig.service_account_json}
                onChange={(e) => setGoogleSheetsConfig((p) => ({ ...p, service_account_json: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gs-ids" className="text-sm font-medium text-gray-900">Spreadsheet IDs (komma-gescheiden)</Label>
              <Input id="gs-ids" required placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms" value={googleSheetsConfig.spreadsheet_ids} onChange={(e) => setGoogleSheetsConfig((p) => ({ ...p, spreadsheet_ids: e.target.value }))} className="rounded-lg border border-gray-200 text-sm" />
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

      {/* ── GitHub form ──────────────────────────────────────────────── */}
      {sourceType === 'github' && (
        <div className="space-y-6">
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
      </div>{/* close mt-6 */}
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
