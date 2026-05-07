import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, ChevronRight, Settings, ChevronDown, AlertTriangle, CheckCircle2, Loader2, Sparkles, Globe, FileText, Shield,
} from 'lucide-react'
import { SiGithub, SiNotion, SiGoogledrive, SiAirtable, SiConfluence, SiGoogledocs, SiGooglesheets, SiGoogleslides } from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect, type MultiSelectOption } from '@/components/ui/multi-select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { MS_SITE_URL_PATTERN } from '@/lib/ms-docs'
import { joinSeedUrl } from './$kbSlug/-kb-helpers'

// -- Types -------------------------------------------------------------------

type ConnectorType =
  | 'github' | 'web_crawler' | 'google_drive' | 'notion' | 'ms_docs'
  | 'airtable' | 'confluence'
  | 'google_docs' | 'google_sheets' | 'google_slides'
// SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-1: web_crawler wizard step order is
// Details → AuthQuestion → AuthSetup (only if requires login) → Selector → Settings.
// AuthSetup runs the REQ-2 auth-probe; Selector gates on the REQ-3 success
// classification. Other connector types (github/notion/...) are unaffected.
type WcStep = 'details' | 'auth-question' | 'auth-setup' | 'selector' | 'settings'

type AuthProbeClassification =
  | 'auth_ok'
  | 'auth_failed_no_cookies'
  | 'auth_failed_still_walled'
  | 'auth_failed_credentials_invalid'
  | 'auth_failed_unreachable'

interface AuthProbeResult {
  classification: AuthProbeClassification
  match_reasons: string[]
  word_count: number
  auth_guard: AuthGuardSuggestion | null
}

type PreviewClassification =
  | 'success'
  | 'selector_required'
  | 'selector_returns_empty'
  | 'requires_javascript'
  | 'auth_wall_detected'
  | 'unknown'

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
  cookies: string
}

interface AuthGuardSuggestion {
  canary_url: string | null
  canary_fingerprint: string | null
  login_indicator_selector: string | null
  login_indicator_description: string | null
}

interface NotionConfig {
  access_token: string
  database_ids: string
  max_pages: string
}

interface AirtableConfig {
  api_key: string
  base_id: string
  table_names: string
  view_name: string
}

interface ConfluenceConfig {
  base_url: string
  email: string
  api_token: string
  space_keys: string
}

const ASSERTION_MODE_OPTIONS: MultiSelectOption[] = [
  { value: 'factual',     label: 'Fact',        description: 'Established fact, documentation, specs' },
  { value: 'procedural',  label: 'Procedure',   description: "Step-by-step instructions, how-to's" },
  { value: 'belief',      label: 'Claim',       description: 'Not conclusively proven claim' },
  { value: 'quoted',      label: 'Quote',       description: 'Literal source material' },
  { value: 'hypothesis',  label: 'Speculation', description: 'Hypotheses, brainstorm' },
  { value: 'unknown',     label: 'Unknown',     description: 'Type not specified' },
]

const CONNECTOR_TYPES: {
  type: ConnectorType
  label: () => string
  available: boolean
  Icon: React.ComponentType<{ className?: string }>
}[] = [
  { type: 'github',        label: m.admin_connectors_type_github,        available: true,  Icon: SiGithub },
  { type: 'web_crawler',  label: m.admin_connectors_type_website,       available: true,  Icon: Globe },
  { type: 'google_drive', label: m.admin_connectors_type_google_drive,  available: true,  Icon: SiGoogledrive },
  { type: 'notion',       label: m.admin_connectors_type_notion,        available: true,  Icon: SiNotion },
  { type: 'ms_docs',      label: m.admin_connectors_type_ms_docs,       available: true,  Icon: FileText },
  { type: 'airtable',     label: m.admin_connectors_type_airtable,      available: true,  Icon: SiAirtable },
  { type: 'confluence',   label: m.admin_connectors_type_confluence,    available: true,  Icon: SiConfluence },
  { type: 'google_docs',  label: m.admin_connectors_type_google_docs,   available: true,  Icon: SiGoogledocs },
  { type: 'google_sheets', label: m.admin_connectors_type_google_sheets, available: true, Icon: SiGooglesheets },
  { type: 'google_slides', label: m.admin_connectors_type_google_slides, available: true, Icon: SiGoogleslides },
]

const MARKDOWN_PROSE_CLASSES = 'overflow-y-auto max-h-64 text-xs [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-[var(--color-foreground)] [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-[var(--color-foreground)] [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-[var(--color-foreground)] [&_h3]:mb-1 [&_p]:text-[var(--color-muted-foreground)] [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-[var(--color-muted-foreground)] [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-[var(--color-muted-foreground)] [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-[var(--color-foreground)] [&_hr]:border-[var(--color-border)] [&_hr]:my-2'

// -- Route -------------------------------------------------------------------

const VALID_PRESELECT_TYPES = new Set<ConnectorType>([
  'github', 'notion', 'google_drive', 'google_docs', 'google_sheets', 'google_slides',
  'airtable', 'confluence', 'ms_docs', 'web_crawler',
])
type AddConnectorSearch = { type?: ConnectorType }

export const Route = createFileRoute('/app/knowledge/$kbSlug_/add-connector')({
  validateSearch: (s: Record<string, unknown>): AddConnectorSearch => ({
    type: (VALID_PRESELECT_TYPES as Set<string>).has(s.type as string)
      ? (s.type as ConnectorType)
      : undefined,
  }),
  component: AddConnectorPage,
})

// -- Component ---------------------------------------------------------------

function AddConnectorPage() {
  const { kbSlug } = Route.useParams()
  const { type: preselectType } = Route.useSearch()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [selectedType, setSelectedType] = useState<ConnectorType | null>(preselectType ?? null)
  const [name, setName] = useState('')
  const [allowedAssertionModes, setAllowedAssertionModes] = useState<string[]>([])
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '',
  })
  const [webcrawlerConfig, setWebcrawlerConfig] = useState<WebCrawlerConfig>({
    base_url: '', path_prefix: '', max_pages: '200', content_selector: '', cookies: '',
  })
  const [notionConfig, setNotionConfig] = useState<NotionConfig>({
    access_token: '', database_ids: '', max_pages: '500',
  })
  const [notionStep, setNotionStep] = useState<'credentials' | 'settings'>('credentials')
  const [airtableConfig, setAirtableConfig] = useState<AirtableConfig>({
    api_key: '', base_id: '', table_names: '', view_name: '',
  })
  const [confluenceConfig, setConfluenceConfig] = useState<ConfluenceConfig>({
    base_url: '', email: '', api_token: '', space_keys: '',
  })
  const [folderId, setFolderId] = useState('')
  // ms_docs (SPEC-KB-MS-DOCS-001): optional site_url + drive_id — both empty = personal OneDrive
  const [msSiteUrl, setMsSiteUrl] = useState('')
  const [msDriveId, setMsDriveId] = useState('')
  const [msSiteUrlError, setMsSiteUrlError] = useState<string | null>(null)

  // Webcrawler wizard state
  const [wcStep, setWcStep] = useState<WcStep>('details')
  const [showAdvancedSelector, setShowAdvancedSelector] = useState(false)
  const [requiresLogin, setRequiresLogin] = useState<boolean | null>(null)
  const [wcPreviewUrl, setWcPreviewUrl] = useState('')
  const [previewResult, setPreviewResult] = useState<{
    fit_markdown: string; word_count: number; warnings: string[]
    content_selector: string | null; selector_source: string | null
    auth_guard: AuthGuardSuggestion | null
    // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3
    classification: PreviewClassification
    classification_reason: string | null
  } | null>(null)
  const [showAdvancedAuthGuard, setShowAdvancedAuthGuard] = useState(false)
  // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2: auth-probe state.
  const [authProbeResult, setAuthProbeResult] = useState<AuthProbeResult | null>(null)
  const [authProbeError, setAuthProbeError] = useState<string | null>(null)

  function parseCookies(): unknown[] | undefined {
    const raw = webcrawlerConfig.cookies.trim()
    if (!raw) return undefined
    // JSON array format: [{"name": "...", "value": "..."}]
    if (raw.startsWith('[')) {
      try { const parsed = JSON.parse(raw); return Array.isArray(parsed) ? parsed : undefined } catch { return undefined }
    }
    // Raw cookie header format: name1=value1; name2=value2
    const domain = (() => { try { return new URL(webcrawlerConfig.base_url).hostname } catch { return '' } })()
    return raw.split(';').map(pair => {
      const [name, ...rest] = pair.trim().split('=')
      return { name: name.trim(), value: rest.join('='), domain, path: '/' }
    }).filter(c => c.name && c.value)
  }

  function goBack() {
    void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug }, search: { tab: 'connectors' } })
  }

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!selectedType) return
      const config: Record<string, unknown> = {}
      if (selectedType === 'github') {
        config.installation_id = Number(githubConfig.installation_id)
        config.repo_owner = githubConfig.repo_owner
        config.repo_name = githubConfig.repo_name
        config.branch = githubConfig.branch
        if (githubConfig.path_filter) config.path_filter = githubConfig.path_filter
      }
      if (selectedType === 'web_crawler') {
        config.base_url = webcrawlerConfig.base_url
        if (webcrawlerConfig.path_prefix) config.path_prefix = webcrawlerConfig.path_prefix
        if (webcrawlerConfig.max_pages && webcrawlerConfig.max_pages !== '200') config.max_pages = Number(webcrawlerConfig.max_pages)
        if (webcrawlerConfig.content_selector) config.content_selector = webcrawlerConfig.content_selector
        const cookies = parseCookies()
        if (cookies) config.cookies = cookies
        // SPEC-CRAWL-004: include auto-detected auth guard values from preview
        const ag = previewResult?.auth_guard
        if (ag?.canary_url) {
          config.canary_url = ag.canary_url
          if (ag.canary_fingerprint) config.canary_fingerprint = ag.canary_fingerprint
        }
        if (ag?.login_indicator_selector) {
          config.login_indicator_selector = ag.login_indicator_selector
        }
      }
      if (selectedType === 'notion') {
        config.access_token = notionConfig.access_token
        const ids = notionConfig.database_ids
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean)
        if (ids.length > 0) config.database_ids = ids
        if (notionConfig.max_pages && notionConfig.max_pages !== '500') config.max_pages = Number(notionConfig.max_pages)
      }
      if (selectedType === 'airtable') {
        config.api_key = airtableConfig.api_key
        config.base_id = airtableConfig.base_id
        config.table_names = airtableConfig.table_names
          .split(',').map((s) => s.trim()).filter(Boolean)
        if (airtableConfig.view_name.trim()) config.view_name = airtableConfig.view_name.trim()
      }
      if (selectedType === 'confluence') {
        config.base_url = confluenceConfig.base_url.replace(/\/$/, '')
        config.email = confluenceConfig.email
        config.api_token = confluenceConfig.api_token
        const keys = confluenceConfig.space_keys.split(',').map((s) => s.trim()).filter(Boolean)
        if (keys.length > 0) config.space_keys = keys
      }
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/`, {
        method: 'POST',
        body: JSON.stringify({
          name,
          connector_type: selectedType,
          config,
          schedule: null,
          allowed_assertion_modes: allowedAssertionModes.length > 0 ? allowedAssertionModes : null,
        }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      goBack()
    },
  })

  const createGoogleDriveMutation = useMutation({
    mutationFn: async () => {
      // google_docs / google_sheets / google_slides are aliases that reuse the google_drive
      // OAuth flow — the backend's GoogleDriveAdapter injects content_types based on
      // connector.connector_type. We pass the selected type as-is.
      const connectorType = selectedType ?? 'google_drive'
      const config: Record<string, unknown> = {}
      if (folderId.trim()) config.folder_id = folderId.trim()
      const result = await apiFetch<{ id: string }>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, {
        method: 'POST',
        body: JSON.stringify({
          name,
          connector_type: connectorType,
          config,
          schedule: null,
          allowed_assertion_modes: allowedAssertionModes.length > 0 ? allowedAssertionModes : null,
        }),
      })
      // Fetch the OAuth authorize URL (authenticated call sets the state cookie).
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/google_drive/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(result.id)}`, )
      return { authorizeUrl: authorize_url }
    },
    onSuccess: ({ authorizeUrl }) => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      // .assign() over `.href =` — consistent with connectors.tsx reconnect flow;
      // react-hooks/immutability flags the property-assignment form.
      window.location.assign(authorizeUrl)
    },
  })

  // SPEC-KB-MS-DOCS-001 R4: Microsoft 365 OAuth flow, mirrors Google Drive.
  // MS_SITE_URL_PATTERN imported from @/lib/ms-docs (shared with edit-connector).
  const createMsDocsMutation = useMutation({
    mutationFn: async () => {
      // Client-side validation (R4.3) before posting.
      const siteUrl = msSiteUrl.trim()
      if (siteUrl && !MS_SITE_URL_PATTERN.test(siteUrl)) {
        setMsSiteUrlError(m.admin_connectors_ms_docs_site_url_invalid())
        throw new Error('invalid_site_url')
      }
      setMsSiteUrlError(null)
      const config: Record<string, unknown> = {}
      if (siteUrl) config.site_url = siteUrl
      if (msDriveId.trim()) config.drive_id = msDriveId.trim()
      const result = await apiFetch<{ id: string }>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, {
        method: 'POST',
        body: JSON.stringify({
          name,
          connector_type: 'ms_docs',
          config,
          schedule: null,
          allowed_assertion_modes: allowedAssertionModes.length > 0 ? allowedAssertionModes : null,
        }),
      })
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/ms_docs/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(result.id)}`, )
      return { authorizeUrl: authorize_url }
    },
    onSuccess: ({ authorizeUrl }) => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      // .assign() over `.href =` — consistent with connectors.tsx reconnect flow;
      // react-hooks/immutability flags the property-assignment form.
      window.location.assign(authorizeUrl)
    },
  })

  const [previewError, setPreviewError] = useState<string | null>(null)

  const previewMutation = useMutation({
    mutationFn: async ({ url, content_selector, try_ai, cookies }: { url: string; content_selector?: string; try_ai?: boolean; cookies?: unknown[] }) => {
      return apiFetch<{
        fit_markdown: string; word_count: number; warnings: string[]; url: string
        content_selector: string | null; selector_source: string | null
        auth_guard: AuthGuardSuggestion | null
        classification: PreviewClassification
        classification_reason: string | null
      }>(`/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`, {
        method: 'POST',
        body: JSON.stringify({ url, content_selector: content_selector || null, try_ai: try_ai ?? false, cookies: cookies || null }),
      })
    },
    onSuccess: (data) => {
      setPreviewResult(data)
      setPreviewError(null)
      // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3: when the page is auth-walled
      // the wizard MUST send the user back to the auth step, not show "configure
      // your selector". Hard-jump rather than letting the user stare at amber.
      if (data.classification === 'auth_wall_detected') {
        setRequiresLogin(true)
        setWcStep('auth-setup')
        setAuthProbeResult(null)
        return
      }
      // Auto-expand CSS selector section when classification points at it.
      if (data.classification === 'selector_required' || data.classification === 'selector_returns_empty') {
        setShowAdvancedSelector(true)
      }
    },
    onError: (err) => { setPreviewError(err instanceof Error ? err.message : 'Preview failed'); setPreviewResult(null) },
  })

  // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2: hits /connectors/auth-probe.
  const authProbeMutation = useMutation({
    mutationFn: async ({ url, cookies }: { url: string; cookies?: unknown[] }) => {
      return apiFetch<AuthProbeResult>(
        `/api/app/knowledge-bases/${kbSlug}/connectors/auth-probe`,
        {
          method: 'POST',
          body: JSON.stringify({ url, cookies: cookies || null }),
        },
      )
    },
    onSuccess: (data) => {
      setAuthProbeResult(data)
      setAuthProbeError(null)
    },
    onError: (err) => {
      setAuthProbeError(err instanceof Error ? err.message : 'Auth probe failed')
      setAuthProbeResult(null)
    },
  })

  // SPEC-CONNECTOR-INPUT-VALIDATION-001 D-10: cache invalidation. Re-verify
  // on any config change that affects the probe / preview outcome.
  function invalidateAuthProbe() {
    setAuthProbeResult(null)
    setAuthProbeError(null)
  }
  function invalidatePreview() {
    setPreviewResult(null)
    setPreviewError(null)
  }

  return (
    <div className="mx-auto max-w-xl px-6 py-10">
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_connectors_add_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_cancel()}
        </Button>
      </div>

      {/* Step indicator — shared component */}
      {(() => {
        const isSimple = selectedType === 'github' || selectedType === 'notion' || selectedType === 'google_drive' || selectedType === 'ms_docs'
          || selectedType === 'airtable' || selectedType === 'confluence'
          || selectedType === 'google_docs' || selectedType === 'google_sheets' || selectedType === 'google_slides'

        const steps: StepItem[] = isSimple
          ? [
              { label: m.admin_connectors_step_type(),      onClick: () => setSelectedType(null) },
              { label: m.admin_connectors_step_configure() },
            ]
          : [
              // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-1: 5-step web_crawler wizard.
              { label: m.admin_connectors_step_type(),                onClick: () => setSelectedType(null) },
              { label: m.admin_connectors_webcrawler_step_details(),  onClick: () => setWcStep('details') },
              { label: 'Authentication',                              onClick: () => setWcStep('auth-question') },
              { label: m.admin_connectors_webcrawler_step_preview(),  onClick: () => setWcStep('selector') },
              { label: m.admin_connectors_webcrawler_step_settings() },
            ]

        const WC_STEP_INDEX: Record<WcStep, number> = {
          details: 1,
          'auth-question': 2,
          'auth-setup': 2,
          selector: 3,
          settings: 4,
        }
        const currentIndex = !selectedType
          ? 0
          : isSimple
            ? 1
            : WC_STEP_INDEX[wcStep]

        return <StepIndicator steps={steps} currentIndex={currentIndex} />
      })()}

      <div className="mt-6 space-y-4">

            {/* Step 1: Type selection */}
            {!selectedType && (
              <div className="grid grid-cols-2 gap-3">
                {CONNECTOR_TYPES.map(({ type, label, available, Icon }) => (
                  <button
                    key={type}
                    type="button"
                    disabled={!available}
                    onClick={() => {
                      if (available) {
                        setSelectedType(type)
                        setWcStep('details')
                        setNotionStep('credentials')
                        setFolderId('')
                        setShowAdvancedSelector(false)
                        setPreviewResult(null)
                        setWcPreviewUrl('')
                      }
                    }}
                    className={[
                      'flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all',
                      !available ? 'cursor-not-allowed opacity-50' : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50',
                    ].join(' ')}
                  >
                    <Icon className="h-4 w-4 text-[var(--color-accent)]" />
                    <span className="text-sm font-medium text-[var(--color-foreground)]">{label()}</span>
                    {!available && <Badge variant="outline" className="text-xs">{m.admin_connectors_coming_soon()}</Badge>}
                  </button>
                ))}
              </div>
            )}

            {/* GitHub form */}
            {selectedType === 'github' && (
              <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="conn-name">{m.admin_connectors_field_name()}</Label>
                  <Input id="conn-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="conn-install">{m.admin_connectors_github_installation_id()}</Label>
                  <Input id="conn-install" type="number" required value={githubConfig.installation_id} onChange={(e) => setGithubConfig((p) => ({ ...p, installation_id: e.target.value }))} />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-owner">{m.admin_connectors_github_repo_owner()}</Label>
                    <Input id="conn-owner" required value={githubConfig.repo_owner} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_owner: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-repo">{m.admin_connectors_github_repo_name()}</Label>
                    <Input id="conn-repo" required value={githubConfig.repo_name} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_name: e.target.value }))} />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="conn-branch">{m.admin_connectors_github_branch()}</Label>
                  <Input id="conn-branch" required placeholder={m.admin_connectors_github_branch_placeholder()} value={githubConfig.branch} onChange={(e) => setGithubConfig((p) => ({ ...p, branch: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                  <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                </div>
                {createMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createMutation.error instanceof Error ? createMutation.error.message : m.admin_connectors_error_create_generic()}
                  </p>
                )}
                <div className="flex gap-2 pt-1">
                  <Button type="submit" size="sm" disabled={createMutation.isPending}>
                    {createMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedType(null)}>
                    {m.admin_connectors_webcrawler_back()}
                  </Button>
                </div>
              </form>
            )}

            {/* Notion form */}
            {selectedType === 'notion' && (
              <div className="space-y-4">
                {/* Step 1: Credentials */}
                {notionStep === 'credentials' && (
                  <form onSubmit={(e) => { e.preventDefault(); setNotionStep('settings') }} className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="notion-name">{m.admin_connectors_field_name()}</Label>
                      <Input id="notion-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="notion-token">{m.admin_connectors_notion_access_token()}</Label>
                      <Input id="notion-token" type="password" required placeholder={m.admin_connectors_notion_access_token_placeholder()} value={notionConfig.access_token} onChange={(e) => setNotionConfig((p) => ({ ...p, access_token: e.target.value }))} />
                      <p className="text-xs text-[var(--color-muted-foreground)]">
                        {m.admin_connectors_notion_token_help_prefix()}{' '}
                        <a
                          href="https://www.notion.so/my-integrations"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-0.5 text-[var(--color-rl-accent-dark)] hover:text-[var(--color-foreground)] underline underline-offset-2"
                        >
                          notion.so/my-integrations
                          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12" fill="none" className="size-3 shrink-0" aria-hidden="true">
                            <path d="M3.5 3H2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V8.5M7 1h4m0 0v4m0-4L5 7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        </a>{' '}
                        {m.admin_connectors_notion_token_help_suffix()}
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="notion-db-ids">{m.admin_connectors_notion_database_ids()}</Label>
                      <textarea
                        id="notion-db-ids"
                        className="flex min-h-[80px] w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-sm placeholder:text-[var(--color-muted-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
                        placeholder={m.admin_connectors_notion_database_ids_placeholder()}
                        value={notionConfig.database_ids}
                        onChange={(e) => setNotionConfig((p) => ({ ...p, database_ids: e.target.value }))}
                      />
                    </div>
                    <div className="flex gap-2 pt-1">
                      <Button type="submit" size="sm" disabled={!name || !notionConfig.access_token}>
                        {m.admin_connectors_webcrawler_next()}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedType(null)}>
                        {m.admin_connectors_webcrawler_back()}
                      </Button>
                    </div>
                  </form>
                )}
                {/* Step 2: Settings */}
                {notionStep === 'settings' && (
                  <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">
                    <div className="space-y-1.5">
                      <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                      <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="notion-max-pages">{m.admin_connectors_notion_max_pages()}</Label>
                      <Input id="notion-max-pages" type="number" min="1" max="2000" value={notionConfig.max_pages} onChange={(e) => setNotionConfig((p) => ({ ...p, max_pages: e.target.value }))} />
                    </div>
                    {createMutation.error && (
                      <p className="text-sm text-[var(--color-destructive)]">
                        {createMutation.error instanceof Error ? createMutation.error.message : m.admin_connectors_error_create_generic()}
                      </p>
                    )}
                    <div className="flex gap-2 pt-1">
                      <Button type="submit" size="sm" disabled={createMutation.isPending}>
                        {createMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setNotionStep('credentials')}>
                        {m.admin_connectors_webcrawler_back()}
                      </Button>
                    </div>
                  </form>
                )}
              </div>
            )}

            {/* Google Drive OAuth flow — also used for google_docs / google_sheets / google_slides aliases */}
            {(selectedType === 'google_drive' || selectedType === 'google_docs' || selectedType === 'google_sheets' || selectedType === 'google_slides') && (
              <form onSubmit={(e) => { e.preventDefault(); createGoogleDriveMutation.mutate() }} className="space-y-3">
                {selectedType === 'google_docs' && (
                  <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_google_docs_subtitle()}</p>
                )}
                {selectedType === 'google_sheets' && (
                  <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_google_sheets_subtitle()}</p>
                )}
                {selectedType === 'google_slides' && (
                  <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_google_slides_subtitle()}</p>
                )}
                <div className="space-y-1.5">
                  <Label htmlFor="gd-name">{m.admin_connectors_field_name()}</Label>
                  <Input id="gd-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="gd-folder-id">{m.admin_connectors_google_drive_folder_id()}</Label>
                  <Input id="gd-folder-id" placeholder={m.admin_connectors_google_drive_folder_id_placeholder()} value={folderId} onChange={(e) => setFolderId(e.target.value)} />
                  <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_google_drive_folder_id_help()}</p>
                </div>
                <div className="space-y-1.5">
                  <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                  <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                </div>
                {createGoogleDriveMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createGoogleDriveMutation.error instanceof Error ? createGoogleDriveMutation.error.message : m.admin_connectors_error_create_generic()}
                  </p>
                )}
                <div className="flex gap-2 pt-1">
                  <Button type="submit" size="sm" disabled={createGoogleDriveMutation.isPending || !name}>
                    {createGoogleDriveMutation.isPending ? m.admin_connectors_google_drive_connecting() : m.admin_connectors_google_drive_connect()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedType(null)}>
                    {m.admin_connectors_webcrawler_back()}
                  </Button>
                </div>
              </form>
            )}

            {/* Microsoft 365 OAuth flow (SPEC-KB-MS-DOCS-001 R4) */}
            {selectedType === 'ms_docs' && (
              <form onSubmit={(e) => { e.preventDefault(); createMsDocsMutation.mutate() }} className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="ms-name">{m.admin_connectors_field_name()}</Label>
                  <Input id="ms-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="ms-site-url">{m.admin_connectors_ms_docs_site_url()}</Label>
                  <Input
                    id="ms-site-url"
                    placeholder="https://contoso.sharepoint.com/sites/marketing"
                    value={msSiteUrl}
                    onChange={(e) => { setMsSiteUrl(e.target.value); setMsSiteUrlError(null) }}
                  />
                  <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_ms_docs_site_url_help()}</p>
                  {msSiteUrlError && (
                    <p className="text-xs text-[var(--color-destructive)]">{msSiteUrlError}</p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="ms-drive-id">{m.admin_connectors_ms_docs_drive_id()}</Label>
                  <Input
                    id="ms-drive-id"
                    placeholder="b!xyz..."
                    value={msDriveId}
                    onChange={(e) => setMsDriveId(e.target.value)}
                  />
                  <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_ms_docs_drive_id_help()}</p>
                </div>
                <div className="space-y-1.5">
                  <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                  <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                </div>
                {createMsDocsMutation.error && createMsDocsMutation.error instanceof Error && createMsDocsMutation.error.message !== 'invalid_site_url' && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createMsDocsMutation.error.message}
                  </p>
                )}
                <div className="flex gap-2 pt-1">
                  <Button type="submit" size="sm" disabled={createMsDocsMutation.isPending || !name}>
                    {createMsDocsMutation.isPending ? m.admin_connectors_ms_docs_connecting() : m.admin_connectors_ms_docs_connect()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedType(null)}>
                    {m.admin_connectors_webcrawler_back()}
                  </Button>
                </div>
              </form>
            )}

            {/* Airtable form (SPEC-KB-CONNECTORS-001 R3.1) */}
            {selectedType === 'airtable' && (
              <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="at-name">{m.admin_connectors_field_name()}</Label>
                  <Input id="at-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="at-api-key">{m.admin_connectors_airtable_api_key_label()}</Label>
                  <Input id="at-api-key" type="password" required placeholder={m.admin_connectors_airtable_api_key_hint()} value={airtableConfig.api_key} onChange={(e) => setAirtableConfig((p) => ({ ...p, api_key: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="at-base-id">{m.admin_connectors_airtable_base_id_label()}</Label>
                  <Input id="at-base-id" required placeholder={m.admin_connectors_airtable_base_id_hint()} value={airtableConfig.base_id} onChange={(e) => setAirtableConfig((p) => ({ ...p, base_id: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="at-tables">{m.admin_connectors_airtable_table_names_label()}</Label>
                  <Input id="at-tables" required placeholder={m.admin_connectors_airtable_table_names_hint()} value={airtableConfig.table_names} onChange={(e) => setAirtableConfig((p) => ({ ...p, table_names: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="at-view">{m.admin_connectors_airtable_view_name_label()}</Label>
                  <Input id="at-view" placeholder={m.admin_connectors_airtable_view_name_hint()} value={airtableConfig.view_name} onChange={(e) => setAirtableConfig((p) => ({ ...p, view_name: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                  <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                </div>
                {createMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createMutation.error instanceof Error ? createMutation.error.message : m.admin_connectors_error_create_generic()}
                  </p>
                )}
                <div className="flex gap-2 pt-1">
                  <Button type="submit" size="sm" disabled={createMutation.isPending || !name || !airtableConfig.api_key || !airtableConfig.base_id || !airtableConfig.table_names}>
                    {createMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedType(null)}>
                    {m.admin_connectors_webcrawler_back()}
                  </Button>
                </div>
              </form>
            )}

            {/* Confluence form (SPEC-KB-CONNECTORS-001 R4.1) */}
            {selectedType === 'confluence' && (
              <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="cf-name">{m.admin_connectors_field_name()}</Label>
                  <Input id="cf-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cf-base-url">{m.admin_connectors_confluence_base_url_label()}</Label>
                  <Input id="cf-base-url" type="url" required placeholder={m.admin_connectors_confluence_base_url_hint()} value={confluenceConfig.base_url} onChange={(e) => setConfluenceConfig((p) => ({ ...p, base_url: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cf-email">{m.admin_connectors_confluence_email_label()}</Label>
                  <Input id="cf-email" type="email" required placeholder="you@company.com" value={confluenceConfig.email} onChange={(e) => setConfluenceConfig((p) => ({ ...p, email: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cf-token">{m.admin_connectors_confluence_api_token_label()}</Label>
                  <Input id="cf-token" type="password" required value={confluenceConfig.api_token} onChange={(e) => setConfluenceConfig((p) => ({ ...p, api_token: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cf-spaces">{m.admin_connectors_confluence_space_keys_label()}</Label>
                  <Input id="cf-spaces" placeholder={m.admin_connectors_confluence_space_keys_hint()} value={confluenceConfig.space_keys} onChange={(e) => setConfluenceConfig((p) => ({ ...p, space_keys: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                  <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                </div>
                {createMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">
                    {createMutation.error instanceof Error ? createMutation.error.message : m.admin_connectors_error_create_generic()}
                  </p>
                )}
                <div className="flex gap-2 pt-1">
                  <Button type="submit" size="sm" disabled={createMutation.isPending || !name || !confluenceConfig.base_url || !confluenceConfig.email || !confluenceConfig.api_token}>
                    {createMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedType(null)}>
                    {m.admin_connectors_webcrawler_back()}
                  </Button>
                </div>
              </form>
            )}

            {/* Web crawler wizard */}
            {selectedType === 'web_crawler' && (
              <div className="space-y-4">
                {/* Step 1: Details */}
                {wcStep === 'details' && (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="wc-name">{m.admin_connectors_field_name()}</Label>
                      <Input id="wc-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="wc-base-url">{m.admin_connectors_webcrawler_base_url()}</Label>
                      <Input id="wc-base-url" type="url" required placeholder={m.admin_connectors_webcrawler_base_url_placeholder()} value={webcrawlerConfig.base_url} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, base_url: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="wc-path-prefix">{m.admin_connectors_webcrawler_path_prefix()}</Label>
                      <Input id="wc-path-prefix" placeholder={m.admin_connectors_webcrawler_path_prefix_placeholder()} value={webcrawlerConfig.path_prefix} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, path_prefix: e.target.value }))} />
                    </div>
                    <div className="flex gap-2 pt-1">
                      <Button
                        type="button"
                        size="sm"
                        disabled={!name || !webcrawlerConfig.base_url}
                        onClick={() => {
                          setWcPreviewUrl(webcrawlerConfig.base_url)
                          invalidateAuthProbe()
                          invalidatePreview()
                          setWcStep('auth-question')
                        }}
                      >
                        {m.admin_connectors_webcrawler_next()}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedType(null)}>
                        {m.admin_connectors_webcrawler_back()}
                      </Button>
                    </div>
                  </div>
                )}

                {/* Step 3: Authentication question — Yes/No only, no other state. */}
                {wcStep === 'auth-question' && (
                  <div className="space-y-4">
                    <div className="rounded-lg border border-[var(--color-border)] p-4 space-y-3">
                      <p className="text-sm font-medium text-[var(--color-foreground)]">
                        Is this site behind a login?
                      </p>
                      <p className="text-xs text-[var(--color-muted-foreground)]">
                        Some knowledge bases require you to be logged in to see the content.
                        We&apos;ll verify either way before letting you save.
                      </p>
                      <div className="flex gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant={requiresLogin === false ? 'default' : 'outline'}
                          onClick={() => {
                            setRequiresLogin(false)
                            setWebcrawlerConfig((p) => ({ ...p, cookies: '' }))
                            invalidateAuthProbe()
                            invalidatePreview()
                          }}
                        >
                          No, it&apos;s public
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant={requiresLogin === true ? 'default' : 'outline'}
                          onClick={() => {
                            setRequiresLogin(true)
                            invalidateAuthProbe()
                            invalidatePreview()
                          }}
                        >
                          Yes, login required
                        </Button>
                      </div>
                    </div>
                    <div className="flex gap-2 pt-1">
                      <Button
                        type="button"
                        size="sm"
                        disabled={requiresLogin === null}
                        onClick={() => {
                          // Public path skips auth-setup; private path runs the probe.
                          setWcStep(requiresLogin ? 'auth-setup' : 'selector')
                        }}
                      >
                        {m.admin_connectors_webcrawler_next()}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setWcStep('details')}>
                        {m.admin_connectors_webcrawler_back()}
                      </Button>
                    </div>
                  </div>
                )}

                {/* Step 4: Auth setup — only reached when requiresLogin === true.
                    Hits REQ-2 /connectors/auth-probe; gates on classification === auth_ok. */}
                {wcStep === 'auth-setup' && (
                  <div className="space-y-4">
                    <div className="rounded-lg border border-[var(--color-border)] p-4 space-y-3">
                      <p className="text-sm font-medium text-[var(--color-foreground)]">
                        Authentication cookies
                      </p>
                      <textarea
                        id="wc-cookies"
                        className="flex min-h-[80px] w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-xs font-mono placeholder:text-[var(--color-muted-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
                        placeholder={m.admin_connectors_webcrawler_cookies_placeholder()}
                        value={webcrawlerConfig.cookies}
                        onChange={(e) => {
                          setWebcrawlerConfig((p) => ({ ...p, cookies: e.target.value }))
                          invalidateAuthProbe()
                          invalidatePreview()
                        }}
                      />
                      <p className="text-xs text-[var(--color-muted-foreground)]">
                        Open the site in your browser, log in, then copy the Cookie value from your
                        browser&apos;s developer tools (Network tab &rarr; any request &rarr; Cookie header).
                      </p>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={authProbeMutation.isPending || !webcrawlerConfig.base_url}
                        onClick={() => {
                          setAuthProbeResult(null)
                          setAuthProbeError(null)
                          authProbeMutation.mutate({
                            url: joinSeedUrl(webcrawlerConfig.base_url, webcrawlerConfig.path_prefix),
                            cookies: parseCookies(),
                          })
                        }}
                      >
                        {authProbeMutation.isPending ? (
                          <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />Testing...</>
                        ) : (
                          'Test authentication'
                        )}
                      </Button>
                    </div>

                    {authProbeError && (
                      <p className="text-sm text-[var(--color-destructive)]">{authProbeError}</p>
                    )}

                    {authProbeResult && (
                      <AuthProbeFeedback result={authProbeResult} />
                    )}

                    <div className="flex gap-2 pt-1">
                      <Button
                        type="button"
                        size="sm"
                        disabled={authProbeResult?.classification !== 'auth_ok'}
                        onClick={() => {
                          // SPEC-CONNECTOR-INPUT-VALIDATION-001 D-2 + D-10: carry auth_guard
                          // forward but do NOT pre-pass the preview gate. The selector step
                          // must run its own preview before the save button unlocks.
                          setPreviewResult({
                            fit_markdown: '',
                            word_count: authProbeResult?.word_count ?? 0,
                            warnings: [],
                            content_selector: null,
                            selector_source: null,
                            auth_guard: authProbeResult?.auth_guard ?? null,
                            classification: 'unknown',
                            classification_reason: null,
                          })
                          setWcStep('selector')
                        }}
                      >
                        {m.admin_connectors_webcrawler_next()}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setWcStep('auth-question')}
                      >
                        {m.admin_connectors_webcrawler_back()}
                      </Button>
                    </div>
                  </div>
                )}

                {/* Step 5: Selector — runs preview, gates on REQ-3 classification === success. */}
                {wcStep === 'selector' && (
                  <div className="space-y-4">
                    {/* Auth status reminder — public sites get a banner here, authenticated
                        sites land here only after auth-setup returned auth_ok. */}
                    {requiresLogin === false && (
                      <div className="flex items-center justify-between rounded-lg border border-[var(--color-border)] px-4 py-3">
                        <div className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
                          <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-success)]" />
                          Public site — no login needed
                        </div>
                        <button
                          type="button"
                          className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
                          onClick={() => setWcStep('auth-question')}
                        >
                          Actually, it needs login
                        </button>
                      </div>
                    )}
                    {requiresLogin === true && authProbeResult?.classification === 'auth_ok' && (
                      <div className="flex items-center justify-between rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 px-4 py-3">
                        <div className="flex items-center gap-2 text-xs text-[var(--color-success)]">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Logged in — cookies verified
                        </div>
                        <button
                          type="button"
                          className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
                          onClick={() => setWcStep('auth-setup')}
                        >
                          Edit cookies
                        </button>
                      </div>
                    )}

                    {/* Preview URL */}
                    <>
                        <div className="space-y-1.5">
                          <Label htmlFor="wc-preview-url">{m.admin_connectors_webcrawler_preview_url()}</Label>
                          <Input
                            id="wc-preview-url"
                            type="url"
                            placeholder={webcrawlerConfig.base_url}
                            value={wcPreviewUrl}
                            onChange={(e) => setWcPreviewUrl(e.target.value)}
                          />
                        </div>

                        {/* Advanced: content selector */}
                        <button
                          type="button"
                          className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
                          onClick={() => setShowAdvancedSelector((p) => !p)}
                        >
                          <Settings className="h-3 w-3" />
                          Content selector
                          {showAdvancedSelector ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                        </button>
                        {showAdvancedSelector && (
                          <div className="pl-4 border-l-2 border-[var(--color-border)] space-y-1.5">
                            <Input
                              id="wc-preview-selector"
                              placeholder={m.admin_connectors_webcrawler_content_selector_placeholder()}
                              value={webcrawlerConfig.content_selector}
                              onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, content_selector: e.target.value }))}
                            />
                            <p className="text-xs text-[var(--color-muted-foreground)]">
                              Only needed if the preview picks up menus instead of the article.
                              Leave empty to let AI detect this automatically.
                            </p>
                          </div>
                        )}
                    </>
                    {/* Run preview + AI-find buttons.
                        SPEC-CONNECTOR-INPUT-VALIDATION-001: AI find is an upfront
                        affordance (operators expect to see it before running anything,
                        not buried inside a failure message). It composes with the
                        classification feedback below — does not contradict it. */}
                    <div className="flex flex-wrap gap-2 items-center">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={previewMutation.isPending || !wcPreviewUrl}
                        onClick={() => {
                          invalidatePreview()
                          previewMutation.mutate({ url: wcPreviewUrl, content_selector: webcrawlerConfig.content_selector, cookies: parseCookies() })
                        }}
                      >
                        {previewMutation.isPending
                          ? <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />{m.admin_connectors_webcrawler_preview_loading()}</>
                          : m.admin_connectors_webcrawler_run_preview()
                        }
                      </Button>
                      {!webcrawlerConfig.content_selector && (
                        <button
                          type="button"
                          className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-accent)] transition-colors disabled:opacity-50"
                          disabled={previewMutation.isPending || !wcPreviewUrl}
                          onClick={() => {
                            invalidatePreview()
                            previewMutation.mutate({ url: wcPreviewUrl, try_ai: true, cookies: parseCookies() })
                          }}
                        >
                          <Sparkles className="h-3 w-3" />
                          {m.admin_connectors_webcrawler_try_ai()}
                        </button>
                      )}
                    </div>
                    {/* Error state */}
                    {previewError && !previewMutation.isPending && (
                      <p className="text-sm text-[var(--color-destructive)]">{previewError}</p>
                    )}
                    {/* Loading state */}
                    {previewMutation.isPending && (
                      <div className="rounded-lg border border-[var(--color-border)] p-4 flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        {m.admin_connectors_webcrawler_preview_loading()}
                      </div>
                    )}
                    {/* Empty state — before any preview has run */}
                    {!previewResult && !previewMutation.isPending && !previewError && (
                      <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_empty()}</p>
                    )}
                    {/* SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3:
                        Single source of truth: classification drives ALL feedback.
                        Supporting fields (fit_markdown, selector_source, auth_guard) compose
                        ALONGSIDE the classification message — never in parallel. */}
                    {previewResult !== null && !previewMutation.isPending && (
                      <div className="space-y-3">
                        {/* Primary classification message */}
                        <PreviewClassificationFeedback
                          classification={previewResult.classification}
                          reason={previewResult.classification_reason}
                          onRetry={() => {
                            invalidatePreview()
                            previewMutation.mutate({ url: wcPreviewUrl, content_selector: webcrawlerConfig.content_selector, cookies: parseCookies() })
                          }}
                        />

                        {/* Affordance: AI-detected selector badge + "Use this selector" CTA.
                            Shown for success and selector_required/empty when AI found something. */}
                        {previewResult.selector_source === 'ai' && previewResult.content_selector && (
                          <div className="rounded-lg border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/5 p-3 space-y-2">
                            <div className="flex gap-2 items-center text-xs text-[var(--color-accent)]">
                              <Sparkles className="h-3.5 w-3.5 shrink-0" />
                              <span>{m.admin_connectors_webcrawler_ai_selector_detected({ selector: previewResult.content_selector, count: String(previewResult.word_count) })}</span>
                            </div>
                            {webcrawlerConfig.content_selector !== previewResult.content_selector && (
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="text-xs h-7"
                                onClick={() => {
                                  setWebcrawlerConfig((p) => ({ ...p, content_selector: previewResult.content_selector! }))
                                  setShowAdvancedSelector(true)
                                }}
                              >
                                {m.admin_connectors_webcrawler_ai_selector_use()}
                              </Button>
                            )}
                          </div>
                        )}

                        {/* Affordance: inline "Try AI find selector" CTA for
                            selector_required / selector_returns_empty when no selector is set
                            and AI was not the source. */}
                        {(previewResult.classification === 'selector_required' || previewResult.classification === 'selector_returns_empty') &&
                          !webcrawlerConfig.content_selector && previewResult.selector_source !== 'ai' && (
                          <button
                            type="button"
                            className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-accent)] transition-colors disabled:opacity-50"
                            disabled={previewMutation.isPending}
                            onClick={() => {
                              invalidatePreview()
                              previewMutation.mutate({ url: wcPreviewUrl, try_ai: true, cookies: parseCookies() })
                            }}
                          >
                            <Sparkles className="h-3 w-3" />
                            {m.admin_connectors_webcrawler_try_ai()}
                          </button>
                        )}

                        {/* Affordance: extracted markdown body as proof — success only.
                            Shows what the crawler will actually store. */}
                        {previewResult.classification === 'success' && previewResult.word_count > 0 && (
                          <div className="rounded-lg border border-[var(--color-border)] p-3 space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-medium text-[var(--color-foreground)]">{m.admin_connectors_webcrawler_preview_title()}</span>
                              <span className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_word_count({ count: String(previewResult.word_count) })}</span>
                            </div>
                            {previewResult.fit_markdown.trim() ? (
                              <div className={MARKDOWN_PROSE_CLASSES}>
                                <ReactMarkdown components={{ a: ({ children }) => <span className="text-[var(--color-accent)]">{children}</span> }}>{previewResult.fit_markdown}</ReactMarkdown>
                              </div>
                            ) : (
                              <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_empty()}</p>
                            )}
                          </div>
                        )}

                        {/* Affordance: SPEC-CRAWL-004 auth guard confirmation block.
                            Only when canary_url was auto-detected and classification is success. */}
                        {previewResult.classification === 'success' && previewResult.auth_guard?.canary_url && (
                          <div className="rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 space-y-2">
                            <div className="flex gap-2 items-center text-xs text-[var(--color-success)]">
                              <Shield className="h-3.5 w-3.5 shrink-0" />
                              <span>Auth protection enabled</span>
                            </div>
                            <p className="text-xs text-[var(--color-muted-foreground)] ml-5.5">
                              We&apos;ll check this page before every sync to detect expired logins.
                              {previewResult.auth_guard.login_indicator_selector && (
                                <> Pages without login indicator will be excluded.</>
                              )}
                            </p>
                            <button
                              type="button"
                              className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors ml-5.5"
                              onClick={() => setShowAdvancedAuthGuard(!showAdvancedAuthGuard)}
                            >
                              <Settings className="h-3 w-3" />
                              Advanced settings
                            </button>
                            {showAdvancedAuthGuard && (
                              <div className="ml-5.5 space-y-2 pt-1">
                                <div className="space-y-1">
                                  <Label className="text-xs">Canary page URL</Label>
                                  <Input
                                    className="text-xs h-7"
                                    value={previewResult.auth_guard.canary_url ?? ''}
                                    onChange={(e) =>
                                      setPreviewResult((p) =>
                                        p ? { ...p, auth_guard: { ...p.auth_guard!, canary_url: e.target.value || null, canary_fingerprint: null } } : p
                                      )
                                    }
                                  />
                                </div>
                                <div className="space-y-1">
                                  <Label className="text-xs">Login indicator selector</Label>
                                  <Input
                                    className="text-xs h-7"
                                    placeholder=".logged-in-user-menu"
                                    value={previewResult.auth_guard.login_indicator_selector ?? ''}
                                    onChange={(e) =>
                                      setPreviewResult((p) =>
                                        p ? { ...p, auth_guard: { ...p.auth_guard!, login_indicator_selector: e.target.value || null } } : p
                                      )
                                    }
                                  />
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                    <div className="flex gap-2 pt-1">
                      <Button
                        type="button"
                        size="sm"
                        disabled={previewResult?.classification !== 'success'}
                        onClick={() => setWcStep('settings')}
                      >
                        {m.admin_connectors_webcrawler_next()}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setWcStep(requiresLogin ? 'auth-setup' : 'auth-question')
                        }
                      >
                        {m.admin_connectors_webcrawler_back()}
                      </Button>
                    </div>
                  </div>
                )}

                {/* Step 6: Settings */}
                {wcStep === 'settings' && (
                  <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">
                    <div className="space-y-1.5">
                      <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                      <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="wc-max-pages">{m.admin_connectors_webcrawler_max_pages()}</Label>
                      <Input id="wc-max-pages" type="number" min="1" max="2000" placeholder={m.admin_connectors_webcrawler_max_pages_placeholder()} value={webcrawlerConfig.max_pages} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, max_pages: e.target.value }))} />
                    </div>
                    {createMutation.error && (
                      <p className="text-sm text-[var(--color-destructive)]">
                        {createMutation.error instanceof Error ? createMutation.error.message : m.admin_connectors_error_create_generic()}
                      </p>
                    )}
                    <div className="flex gap-2 pt-1">
                      <Button type="submit" size="sm" disabled={createMutation.isPending}>
                        {createMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setWcStep('selector')}>{m.admin_connectors_webcrawler_back()}</Button>
                    </div>
                  </form>
                )}
              </div>
            )}

      </div>
    </div>
  )
}

// -- Helper components -------------------------------------------------------

/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 — render auth-probe outcome.
 * Shared by add-connector and edit-connector flows.
 */
export function AuthProbeFeedback({ result }: { result: AuthProbeResult }) {
  if (result.classification === 'auth_ok') {
    return (
      <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 text-xs text-[var(--color-success)]">
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
        <span>You&apos;re in. Continue to Selector.</span>
      </div>
    )
  }
  const reasons = result.match_reasons.length > 0
    ? ` Detected: ${result.match_reasons.join(', ')}`
    : ''
  let message: string
  switch (result.classification) {
    case 'auth_failed_no_cookies':
      message = 'This page requires authentication. Go back to step 3 and answer Yes.'
      break
    case 'auth_failed_still_walled':
      message = `Cookies didn't unlock the content. Re-paste a fresh session cookie.${reasons}`
      break
    case 'auth_failed_credentials_invalid':
      message = '401/403 — credentials rejected.'
      break
    case 'auth_failed_unreachable':
      message = 'Could not reach the page. Check the Base URL.'
      break
    default:
      message = `Authentication check failed.${reasons}`
  }
  return (
    <div className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  )
}

/**
 * SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 — render preview classification outcome.
 * Single source of truth for all classification-driven feedback.
 * Supporting affordances (markdown body, AI selector, auth-guard) compose alongside
 * via the parent — this component only renders the primary message.
 */
export function PreviewClassificationFeedback({
  classification,
  reason,
  onRetry,
}: {
  classification: PreviewClassification
  reason: string | null
  onRetry?: () => void
}) {
  if (classification === 'success') {
    return (
      <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 text-xs text-[var(--color-success)]">
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
        <span>Selector matches real article content. You can save the connector.</span>
      </div>
    )
  }
  let message: string
  switch (classification) {
    case 'selector_required':
      message =
        reason ?? 'The output looks like a navigation menu. Configure a Content Selector.'
      break
    case 'selector_returns_empty':
      message = "Selector matched no content. Try a different selector or click 'Let AI find'."
      break
    case 'requires_javascript':
      message =
        'Page renders via JavaScript. Configure a wait_for condition or selector for the post-render DOM.'
      break
    case 'auth_wall_detected':
      message = 'This page requires authentication. Go back to step 4.'
      break
    case 'unknown':
      message = reason ?? 'Preview service did not respond. Try again.'
      break
    default:
      message = reason ?? 'Selector check failed.'
  }
  return (
    <div className="space-y-2">
      <div className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
        <span>{message}</span>
      </div>
      {classification === 'unknown' && onRetry && (
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
          onClick={onRetry}
        >
          Retry
        </button>
      )}
    </div>
  )
}
