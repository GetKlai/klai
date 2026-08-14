import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, ChevronRight, Settings, ChevronDown, CheckCircle2, Loader2, Sparkles, Globe, FileText, Shield,
} from 'lucide-react'
import { SiGithub, SiNotion, SiGoogledrive, SiAirtable, SiConfluence } from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { MS_SITE_URL_PATTERN } from '@/lib/ms-docs'
import type { CookieRow } from './$kbSlug/-kb-types'
import type {
  AirtableConfig,
  AuthGuardSuggestion,
  AuthProbeResult,
  ConfluenceConfig,
  ConnectorType,
  GitHubConfig,
  NotionAddConfig,
  PreviewResult,
  WcStep,
  WebCrawlerConfig,
} from './-connector-types'
import {
  joinSeedUrl,
  MARKDOWN_PROSE_CLASSES,
  normalizeConnectorPreselectType,
  previewUrlOnDetailsAdvance,
  VALID_PRESELECT_TYPES,
} from './-connector-constants'
import { AuthProbeFeedback, PreviewClassificationFeedback } from './-connector-feedback'
import { CookieRowsInput } from '@/components/knowledge/CookieRowsInput'
import { kbQueryKeys } from '@/lib/kb-query-keys'

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
]

// -- Route -------------------------------------------------------------------

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

  const [selectedType, setSelectedType] = useState<ConnectorType | null>(
    normalizeConnectorPreselectType(preselectType) ?? null,
  )
  const [name, setName] = useState('')
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '',
  })
  const [webcrawlerConfig, setWebcrawlerConfig] = useState<WebCrawlerConfig>({
    base_url: '', path_prefix: '', max_pages: '200', content_selector: '',
  })
  // Cookies live in their own state as structured {name, value} rows. The
  // wizard collects them directly in the shape the backend persists and the
  // cron-sync consumes - no string-to-array parsing layer.
  const [wcCookieRows, setWcCookieRows] = useState<CookieRow[]>([])
  const [notionConfig, setNotionConfig] = useState<NotionAddConfig>({
    access_token: '', database_ids: '', max_pages: '500',
  })
  const [notionStep, setNotionStep] = useState<'credentials' | 'settings'>('credentials')
  const [airtableConfig, setAirtableConfig] = useState<AirtableConfig>({
    api_key: '', base_id: '', table_names: '', view_name: '',
  })
  const [confluenceConfig, setConfluenceConfig] = useState<ConfluenceConfig>({
    base_url: '', email: '', api_token: '', space_keys: '',
  })
  // ms_docs (SPEC-KB-MS-DOCS-001): optional site_url + drive_id - both empty = personal OneDrive
  const [msSiteUrl, setMsSiteUrl] = useState('')
  const [msDriveId, setMsDriveId] = useState('')
  const [msSiteUrlError, setMsSiteUrlError] = useState<string | null>(null)
  // ms_docs: site_url + drive_id are power-user fields. Hidden behind an
  // "Advanced" disclosure so a normal user just sees name + Connect (both
  // empty = sync the personal OneDrive, the common case).
  const [showMsAdvanced, setShowMsAdvanced] = useState(false)

  // Webcrawler wizard state
  const [wcStep, setWcStep] = useState<WcStep>('details')
  const [showAdvancedSelector, setShowAdvancedSelector] = useState(false)
  const [requiresLogin, setRequiresLogin] = useState<boolean | null>(null)
  const [wcPreviewUrl, setWcPreviewUrl] = useState('')
  // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3: classification + classification_reason
  // surface the preview-pipeline judgement to the operator. Type lives in
  // ./-connector-types so add and edit share one shape.
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [showAdvancedAuthGuard, setShowAdvancedAuthGuard] = useState(false)
  // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2: auth-probe state.
  const [authProbeResult, setAuthProbeResult] = useState<AuthProbeResult | null>(null)
  const [authProbeError, setAuthProbeError] = useState<string | null>(null)
  // Save-time auth_guard config - operator-editable. Initialized from the
  // auth-probe at step 4 → 5 transition, overwritten by preview's auth_guard
  // on a successful Run preview, written by the editable form below the
  // success message. Save reads this. Lives outside ``previewResult`` so we
  // don't have to fake a ``classification: 'unknown'`` state on the bridge
  // (which used to render an amber "Preview service did not respond" before
  // the operator clicked anything - SPEC-CONNECTOR-INPUT-VALIDATION-001 D-2
  // pre-pop bug).
  const [authGuard, setAuthGuard] = useState<AuthGuardSuggestion | null>(null)

  function buildCookies(): unknown[] | undefined {
    // Filter out empty rows (operator clicked "+ Add another cookie" but didn't
    // fill it in). Domain + path are derived from base_url at submit time so
    // operators don't have to know about URL hostnames.
    const filled = wcCookieRows.filter((r) => r.name.trim() && r.value.trim())
    if (filled.length === 0) return undefined
    const domain = (() => {
      try { return new URL(webcrawlerConfig.base_url).hostname } catch { return '' }
    })()
    return filled.map((r) => ({
      name: r.name.trim(),
      value: r.value.trim(),
      domain,
      path: '/',
    }))
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
        // Discovery seed: when the operator validated a specific interior page
        // in the preview (a detail page, distinct from the base URL), remember
        // it as a fallback crawl seed. The sync starts from base_url; only if
        // that discovers nothing does it fall back to this known-good page.
        // The preview URL stays a render-test — this is a separate config value.
        if (
          wcPreviewUrl &&
          wcPreviewUrl !== webcrawlerConfig.base_url &&
          previewResult?.classification === 'success'
        ) {
          config.discovery_seed_url = wcPreviewUrl
        }
        const cookies = buildCookies()
        if (cookies) config.cookies = cookies
        // SPEC-CRAWL-004: include auto-detected auth guard values. Source is
        // ``authGuard`` state - initialized from auth-probe at step 4 → 5
        // bridge, refreshed by preview onSuccess, mutated by the operator-
        // editable form on step 5.
        const ag = authGuard
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
        }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) })
      goBack()
    },
  })

  const createGoogleDriveMutation = useMutation({
    mutationFn: async () => {
      const config: Record<string, unknown> = {}
      const result = await apiFetch<{ id: string }>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, {
        method: 'POST',
        body: JSON.stringify({
          name,
          connector_type: 'google_drive',
          config,
          schedule: null,
        }),
      })
      // Fetch the OAuth authorize URL (authenticated call sets the state cookie).
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/google_drive/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(result.id)}`, )
      return { authorizeUrl: authorize_url }
    },
    onSuccess: ({ authorizeUrl }) => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) })
      // .assign() over `.href =` - consistent with connectors.tsx reconnect flow;
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
        }),
      })
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/ms_docs/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(result.id)}`, )
      return { authorizeUrl: authorize_url }
    },
    onSuccess: ({ authorizeUrl }) => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) })
      // .assign() over `.href =` - consistent with connectors.tsx reconnect flow;
      // react-hooks/immutability flags the property-assignment form.
      window.location.assign(authorizeUrl)
    },
  })

  const [previewError, setPreviewError] = useState<string | null>(null)

  const previewMutation = useMutation({
    mutationFn: async ({ url, content_selector, try_ai, cookies }: { url: string; content_selector?: string; try_ai?: boolean; cookies?: unknown[] }) => {
      // Use PreviewResult itself rather than restating its shape: this
      // duplicate silently drifted when the backend gained the site-sample
      // counts, and only surfaced as a type error at build time.
      return apiFetch<PreviewResult & { url: string }>(`/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`, {
        method: 'POST',
        body: JSON.stringify({ url, content_selector: content_selector || null, try_ai: try_ai ?? false, cookies: cookies || null }),
      })
    },
    onSuccess: (data) => {
      setPreviewResult(data)
      setPreviewError(null)
      // Preview is the freshest signal of effective auth state, so let its
      // ``auth_guard`` overwrite whatever the auth-probe seeded earlier.
      // Note: we DO update on auth_wall_detected too - a null auth_guard is
      // still meaningful (operator hasn't proven login yet → save will be
      // blocked by classification check anyway).
      setAuthGuard(data.auth_guard)
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
    <div className="mx-auto max-w-xl px-6 pt-4 pb-10">
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

      {/* Step indicator - shared component */}
      {(() => {
        const isSimple = selectedType === 'github' || selectedType === 'notion' || selectedType === 'google_drive' || selectedType === 'ms_docs'
          || selectedType === 'airtable' || selectedType === 'confluence'

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
                        setShowAdvancedSelector(false)
                        setPreviewResult(null)
                        setWcPreviewUrl('')
                      }
                    }}
                    className={[
                      'flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all',
                      !available ? 'cursor-not-allowed opacity-50' : 'border-gray-200 bg-[var(--color-card)] hover:border-gray-300',
                    ].join(' ')}
                  >
                    <Icon className="h-4 w-4 text-gray-400" />
                    <span className="text-sm font-medium text-gray-900">{label()}</span>
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
                </div>                {createMutation.error && (
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
                      <p className="text-xs text-gray-400">
                        {m.admin_connectors_notion_token_help_prefix()}{' '}
                        <a
                          href="https://www.notion.so/my-integrations"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-0.5 text-[var(--color-rl-accent-dark)] hover:text-gray-900 underline underline-offset-2"
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
                        className="flex min-h-[80px] w-full rounded-md border border-gray-200 bg-[var(--color-input)] px-3 py-2 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
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
                  <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">                    <div className="space-y-1.5">
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

            {/* Google Drive OAuth flow. Docs, Sheets and Slides are selected inside the Drive picker after auth. */}
            {selectedType === 'google_drive' && (
              <form onSubmit={(e) => { e.preventDefault(); createGoogleDriveMutation.mutate() }} className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="gd-name">{m.admin_connectors_field_name()}</Label>
                  <Input id="gd-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="rounded-lg border border-gray-200 bg-white px-4 py-3">
                  <div className="flex items-start gap-3">
                    <SiGoogledrive className="mt-0.5 h-5 w-5 shrink-0 text-gray-400" />
                    <div className="space-y-3">
                      <div className="space-y-1">
                        <p className="text-sm font-medium text-gray-900">
                          {m.admin_connectors_google_drive_picker_title()}
                        </p>
                        <p className="text-xs leading-5 text-gray-400">
                          {m.admin_connectors_google_drive_picker_body()}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {['Docs', 'Sheets', 'Slides', 'PDF', 'Office', 'Text'].map((type) => (
                          <span
                            key={type}
                            className="rounded-md border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] leading-4 text-gray-500"
                          >
                            {type}
                          </span>
                        ))}
                      </div>
                      <div className="grid gap-2 text-xs text-gray-500 sm:grid-cols-3">
                        <span className="flex items-center gap-1.5">
                          <Shield className="h-3.5 w-3.5 text-gray-400" />
                          {m.admin_connectors_google_drive_flow_auth()}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <FileText className="h-3.5 w-3.5 text-gray-400" />
                          {m.admin_connectors_google_drive_flow_select()}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <CheckCircle2 className="h-3.5 w-3.5 text-gray-400" />
                          {m.admin_connectors_google_drive_flow_sync()}
                        </span>
                      </div>
                    </div>
                  </div>
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
                <button
                  type="button"
                  className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors"
                  onClick={() => setShowMsAdvanced((p) => !p)}
                >
                  <Settings className="h-3 w-3" />
                  {m.knowledge_detail_tab_advanced()}
                  {showMsAdvanced ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                </button>
                {showMsAdvanced && (
                  <div className="pl-4 border-l-2 border-gray-200 space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="ms-site-url">{m.admin_connectors_ms_docs_site_url()}</Label>
                      <Input
                        id="ms-site-url"
                        placeholder="https://contoso.sharepoint.com/sites/marketing"
                        value={msSiteUrl}
                        onChange={(e) => { setMsSiteUrl(e.target.value); setMsSiteUrlError(null) }}
                      />
                      <p className="text-xs text-gray-400">{m.admin_connectors_ms_docs_site_url_help()}</p>
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
                      <p className="text-xs text-gray-400">{m.admin_connectors_ms_docs_drive_id_help()}</p>
                    </div>
                  </div>
                )}
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
                </div>                {createMutation.error && (
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
                </div>                {createMutation.error && (
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
                          setWcPreviewUrl((current) =>
                            previewUrlOnDetailsAdvance(current, webcrawlerConfig.base_url),
                          )
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

                {/* Step 3: Authentication question - login mode selection. */}
                {wcStep === 'auth-question' && (
                  <div className="space-y-4">
                    <div className="rounded-lg border border-gray-200 p-4 space-y-3">
                      <p className="text-sm font-medium text-gray-900">
                        Is this site behind a login?
                      </p>
                      <p className="text-xs text-gray-400">
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
                            setWcCookieRows([])
                            invalidateAuthProbe()
                            invalidatePreview()
                          }}
                        >
                          Public site
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
                          Login required
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

                {/* Step 4: Auth setup - only reached when requiresLogin === true.
                    Hits REQ-2 /connectors/auth-probe; gates on classification === auth_ok. */}
                {wcStep === 'auth-setup' && (
                  <div className="space-y-4">
                    <div className="rounded-lg border border-gray-200 p-4 space-y-3">
                      <p className="text-sm font-medium text-gray-900">
                        Authentication cookies
                      </p>
                      <CookieRowsInput
                        idPrefix="add-wc-cookie"
                        value={wcCookieRows}
                        onChange={(rows) => {
                          setWcCookieRows(rows)
                          invalidateAuthProbe()
                          invalidatePreview()
                        }}
                      />
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
                            cookies: buildCookies(),
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
                          // Carry auth_guard forward in its own state slot - selector step
                          // (5) starts EMPTY (previewResult stays null) so no amber
                          // "Preview service did not respond" renders before the operator
                          // has clicked Run preview. Save reads ``authGuard`` directly.
                          setAuthGuard(authProbeResult?.auth_guard ?? null)
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

                {/* Step 5: Selector - runs preview, gates on REQ-3 classification === success. */}
                {wcStep === 'selector' && (
                  <div className="space-y-4">
                    {/* Auth status reminder - public sites get a banner here, authenticated
                        sites land here only after auth-setup returned auth_ok. */}
                    {requiresLogin === false && (
                      <div className="flex items-center justify-between rounded-lg border border-gray-200 px-4 py-3">
                        <div className="flex items-center gap-2 text-xs text-gray-400">
                          <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-success)]" />
                          Public site - no login needed
                        </div>
                        <button
                          type="button"
                          className="text-xs text-gray-400 hover:text-gray-900"
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
                          Logged in - cookies verified
                        </div>
                        <button
                          type="button"
                          className="text-xs text-gray-400 hover:text-gray-900"
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
                          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors"
                          onClick={() => setShowAdvancedSelector((p) => !p)}
                        >
                          <Settings className="h-3 w-3" />
                          Content selector
                          {showAdvancedSelector ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                        </button>
                        {showAdvancedSelector && (
                          <div className="pl-4 border-l-2 border-gray-200 space-y-1.5">
                            <Input
                              id="wc-preview-selector"
                              placeholder={m.admin_connectors_webcrawler_content_selector_placeholder()}
                              value={webcrawlerConfig.content_selector}
                              onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, content_selector: e.target.value }))}
                            />
                            <p className="text-xs text-gray-400">
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
                        classification feedback below - does not contradict it. */}
                    <div className="flex flex-wrap gap-2 items-center">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={previewMutation.isPending || !wcPreviewUrl}
                        onClick={() => {
                          invalidatePreview()
                          previewMutation.mutate({ url: wcPreviewUrl, content_selector: webcrawlerConfig.content_selector, cookies: buildCookies() })
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
                          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors disabled:opacity-50"
                          disabled={previewMutation.isPending || !wcPreviewUrl}
                          onClick={() => {
                            invalidatePreview()
                            previewMutation.mutate({ url: wcPreviewUrl, try_ai: true, cookies: buildCookies() })
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
                      <div className="rounded-lg border border-gray-200 p-4 flex items-center gap-2 text-sm text-gray-400">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        {m.admin_connectors_webcrawler_preview_loading()}
                      </div>
                    )}
                    {/* Empty state - before any preview has run */}
                    {!previewResult && !previewMutation.isPending && !previewError && (
                      <p className="text-sm text-gray-400">{m.admin_connectors_webcrawler_preview_empty()}</p>
                    )}
                    {/* SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3:
                        Single source of truth: classification drives ALL feedback.
                        Supporting fields (fit_markdown, selector_source, auth_guard) compose
                        ALONGSIDE the classification message - never in parallel. */}
                    {previewResult !== null && !previewMutation.isPending && (
                      <div className="space-y-3">
                        {/* Primary classification message */}
                        <PreviewClassificationFeedback
                          classification={previewResult.classification}
                          reason={previewResult.classification_reason}
                          onRetry={() => {
                            invalidatePreview()
                            previewMutation.mutate({ url: wcPreviewUrl, content_selector: webcrawlerConfig.content_selector, cookies: buildCookies() })
                          }}
                        />

                        {/* Affordance: AI-detected selector badge + "Use this selector" CTA.
                            Shown for success and selector_required/empty when AI found something. */}
                        {previewResult.selector_source === 'ai' && previewResult.content_selector && (
                          <div className="rounded-lg border border-gray-200 bg-black/[0.06] p-3 space-y-2">
                            <div className="flex gap-2 items-center text-xs text-gray-400">
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
                          !webcrawlerConfig.content_selector &&
                          previewResult.selector_source !== 'ai' &&
                          previewResult.selector_source !== 'ai_failed' && (
                          <button
                            type="button"
                            className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors disabled:opacity-50"
                            disabled={previewMutation.isPending}
                            onClick={() => {
                              invalidatePreview()
                              previewMutation.mutate({ url: wcPreviewUrl, try_ai: true, cookies: buildCookies() })
                            }}
                          >
                            <Sparkles className="h-3 w-3" />
                            {m.admin_connectors_webcrawler_try_ai()}
                          </button>
                        )}

                        {/* Affordance: extracted markdown body as proof - success only.
                            Shows what the crawler will actually store. */}
                        {previewResult.classification === 'success' && previewResult.word_count > 0 && (
                          <div className="rounded-lg border border-gray-200 p-3 space-y-2">
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-medium text-gray-900">{m.admin_connectors_webcrawler_preview_title()}</span>
                              <span className="text-xs text-gray-400">{m.admin_connectors_webcrawler_preview_word_count({ count: String(previewResult.word_count) })}</span>
                            </div>
                            {previewResult.fit_markdown.trim() ? (
                              <div className={MARKDOWN_PROSE_CLASSES}>
                                <ReactMarkdown components={{ a: ({ children }) => <span className="text-gray-400">{children}</span> }}>{previewResult.fit_markdown}</ReactMarkdown>
                              </div>
                            ) : (
                              <p className="text-sm text-gray-400">{m.admin_connectors_webcrawler_preview_empty()}</p>
                            )}
                          </div>
                        )}

                        {/* Affordance: SPEC-CRAWL-004 auth guard confirmation block.
                            Only after a successful preview AND when we have an
                            auth_guard to work with (either fresh from preview or
                            carried over from auth-probe). Source of truth is
                            ``authGuard`` state - operator edits flow there, save
                            reads from there. */}
                        {previewResult.classification === 'success' && authGuard?.canary_url && (
                          <div className="rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 space-y-2">
                            <div className="flex gap-2 items-center text-xs text-[var(--color-success)]">
                              <Shield className="h-3.5 w-3.5 shrink-0" />
                              <span>Auth protection enabled</span>
                            </div>
                            <p className="text-xs text-gray-400 ml-5.5">
                              We&apos;ll check this page before every sync to detect expired logins.
                              {authGuard.login_indicator_selector && (
                                <> Pages without login indicator will be excluded.</>
                              )}
                            </p>
                            <button
                              type="button"
                              className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-900 transition-colors ml-5.5"
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
                                    value={authGuard.canary_url ?? ''}
                                    onChange={(e) =>
                                      setAuthGuard((prev) =>
                                        prev ? { ...prev, canary_url: e.target.value || null, canary_fingerprint: null } : prev
                                      )
                                    }
                                  />
                                </div>
                                <div className="space-y-1">
                                  <Label className="text-xs">Login indicator selector</Label>
                                  <Input
                                    className="text-xs h-7"
                                    placeholder=".logged-in-user-menu"
                                    value={authGuard.login_indicator_selector ?? ''}
                                    onChange={(e) =>
                                      setAuthGuard((prev) =>
                                        prev ? { ...prev, login_indicator_selector: e.target.value || null } : prev
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
                        disabled={!previewResult || previewResult.classification === 'auth_wall_detected'}
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
                  <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">                    <div className="space-y-1.5">
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

// AuthProbeFeedback + PreviewClassificationFeedback live in
// ./-connector-feedback (shared with edit-connector + tested in
// __tests__/wizard-feedback.test.tsx).
