import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, Shield,
  CheckCircle2, Loader2, Sparkles, Settings, ChevronDown, ChevronRight,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { MS_SITE_URL_PATTERN } from '@/lib/ms-docs'
import { ASSERTION_MODE_OPTIONS, joinSeedUrl } from './$kbSlug/-kb-helpers'
import type { ConnectorSummary, CookieRow, GitHubConfig, WebCrawlerConfig } from './$kbSlug/-kb-types'
import { CookieRowsInput } from '@/components/knowledge/CookieRowsInput'
import {
  AuthProbeFeedback,
  PreviewClassificationFeedback,
} from './$kbSlug_.add-connector'
import { kbQueryKeys } from '@/lib/kb-query-keys'

// SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-1 / REQ-5: edit wizard uses the same
// 5-step flow as add-connector. ?step=auth|selector deep-link into the wizard.
// SPEC D-1: always require re-verify on edit entry (no pre-pass from last_sync_status).
type WcStep = 'details' | 'auth-question' | 'auth-setup' | 'selector' | 'settings'

type StepDeepLink = 'auth' | 'selector'
const VALID_STEPS = new Set<StepDeepLink>(['auth', 'selector'])

function _stepToWcStep(step: StepDeepLink | undefined): WcStep | undefined {
  if (step === 'auth') return 'auth-setup'
  if (step === 'selector') return 'selector'
  return undefined
}

type EditSearch = { step?: StepDeepLink }

export const Route = createFileRoute('/app/knowledge/$kbSlug_/edit-connector/$connectorId')({
  validateSearch: (search: Record<string, unknown>): EditSearch => ({
    step: (VALID_STEPS as Set<string>).has(search.step as string)
      ? (search.step as StepDeepLink)
      : undefined,
  }),
  component: EditConnectorPage,
})

interface NotionEditConfig {
  database_ids: string
  max_pages: string
  new_access_token: string
}

interface AirtableEditConfig {
  api_key: string
  base_id: string
  table_names: string
  view_name: string
}

interface ConfluenceEditConfig {
  base_url: string
  email: string
  api_token: string
  space_keys: string
}

type PreviewClassification =
  | 'success'
  | 'selector_required'
  | 'selector_returns_empty'
  | 'requires_javascript'
  | 'auth_wall_detected'
  | 'unknown'

interface AuthGuardSuggestion {
  canary_url: string | null
  canary_fingerprint: string | null
  login_indicator_selector: string | null
  login_indicator_description: string | null
}

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

type PreviewResult = {
  fit_markdown: string
  word_count: number
  warnings: string[]
  content_selector: string | null
  selector_source: string | null
  auth_guard: AuthGuardSuggestion | null
  classification: PreviewClassification
  classification_reason: string | null
}

const MARKDOWN_PROSE_CLASSES = 'overflow-y-auto max-h-64 text-xs [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-gray-900 [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-gray-900 [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-gray-900 [&_h3]:mb-1 [&_p]:text-gray-400 [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-gray-400 [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-gray-400 [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-gray-900 [&_hr]:border-gray-200 [&_hr]:my-2'


function EditConnectorPage() {
  const { kbSlug, connectorId } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate()
  const auth = useAuth()
  const queryClient = useQueryClient()

  function goBack() {
    void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug }, search: { tab: 'connectors' } })
  }

  const { data: connectors = [] } = useQuery<ConnectorSummary[]>({
    queryKey: kbQueryKeys.connectorsPortal(kbSlug),
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`),
    enabled: auth.isAuthenticated,
  })

  const connector = connectors.find((c) => c.id === connectorId)

  const [name, setName] = useState('')
  const [allowedAssertionModes, setAllowedAssertionModes] = useState<string[]>([])
  const [webcrawlerConfig, setWebcrawlerConfig] = useState<WebCrawlerConfig>({
    base_url: '', path_prefix: '', max_pages: '200', content_selector: '',
  })
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '',
  })
  const [notionConfig, setNotionConfig] = useState<NotionEditConfig>({
    database_ids: '', max_pages: '500', new_access_token: '',
  })
  const [folderId, setFolderId] = useState('')
  const [isReconnecting, setIsReconnecting] = useState(false)
  // ms_docs (SPEC-KB-MS-DOCS-001 R4.4): optional site_url + drive_id
  const [msSiteUrl, setMsSiteUrl] = useState('')
  const [msDriveId, setMsDriveId] = useState('')
  const [msSiteUrlError, setMsSiteUrlError] = useState<string | null>(null)
  // airtable (SPEC-KB-CONNECTORS-001 R3)
  const [airtableConfig, setAirtableConfig] = useState<AirtableEditConfig>({
    api_key: '', base_id: '', table_names: '', view_name: '',
  })
  // confluence (SPEC-KB-CONNECTORS-001 R4)
  const [confluenceConfig, setConfluenceConfig] = useState<ConfluenceEditConfig>({
    base_url: '', email: '', api_token: '', space_keys: '',
  })

  // -- Web crawler wizard state (SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-1) ----
  // SPEC D-1: deep-link via ?step=auth opens at auth-setup (pre-set requiresLogin=true),
  // ?step=selector opens at selector. No step → start at details.
  const [wcStep, setWcStep] = useState<WcStep>(_stepToWcStep(search.step) ?? 'details')
  const [showAdvancedSelector, setShowAdvancedSelector] = useState(false)
  const [showAdvancedAuthGuard, setShowAdvancedAuthGuard] = useState(false)
  const [requiresLogin, setRequiresLogin] = useState<boolean | null>(
    search.step === 'auth' ? true : null,
  )
  const [wcPreviewUrl, setWcPreviewUrl] = useState('')
  // Cookies live as structured {name, value} rows — same shape as the
  // backend persists and the cron-sync consumes. No parser layer.
  const [wcCookieRows, setWcCookieRows] = useState<CookieRow[]>([])
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  // SPEC D-2: auth probe for the edit wizard
  const [authProbeResult, setAuthProbeResult] = useState<AuthProbeResult | null>(null)
  const [authProbeError, setAuthProbeError] = useState<string | null>(null)
  // Save-time auth_guard config — operator-editable. Initialized from the
  // auth-probe at step 4 → 5 transition, overwritten by preview's auth_guard
  // on a successful Run preview, written by the editable form below the
  // success message. Save reads this. Lives outside ``previewResult`` so we
  // don't have to fake a ``classification: 'unknown'`` state on the bridge
  // (which used to render an amber "Preview service did not respond" before
  // the operator clicked anything — SPEC-CONNECTOR-INPUT-VALIDATION-001 D-2
  // pre-pop bug).
  const [authGuard, setAuthGuard] = useState<AuthGuardSuggestion | null>(null)

  async function handleGoogleDriveReconnect() {
    setIsReconnecting(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/google_drive/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(connectorId)}`, )
      // .assign() over `.href =` — consistent with connectors.tsx + add-connector;
      // react-hooks/immutability flags the property-assignment form.
      window.location.assign(authorize_url)
    } finally {
      setIsReconnecting(false)
    }
  }

  // SPEC-KB-MS-DOCS-001 R4.4 — trigger a fresh OAuth flow when refresh_token is invalid.
  async function handleMsDocsReconnect() {
    setIsReconnecting(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/ms_docs/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(connectorId)}`, )
      window.location.assign(authorize_url)
    } finally {
      setIsReconnecting(false)
    }
  }

  function buildCookies(): unknown[] | undefined {
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

  useEffect(() => {
    if (!connector) return
    setName(connector.name)
    setAllowedAssertionModes(connector.allowed_assertion_modes ?? [])
    if (connector.connector_type === 'web_crawler') {
      const cfg = connector.config as {
        base_url?: string; path_prefix?: string; max_pages?: number; content_selector?: string
        cookies?: unknown[]; login_indicator_selector?: string
      }
      setWebcrawlerConfig({
        base_url: String(cfg.base_url ?? ''),
        path_prefix: String(cfg.path_prefix ?? ''),
        max_pages: String(cfg.max_pages ?? '200'),
        content_selector: cfg.content_selector ?? '',
      })
      // Pre-seed cookie rows from saved config — same shape, no parsing needed.
      // Existing connectors store cookies as {name, value, domain, path} objects;
      // the wizard only displays/edits name + value (domain + path are derived
      // from base_url at submit time).
      if (cfg.cookies && Array.isArray(cfg.cookies) && cfg.cookies.length > 0) {
        setWcCookieRows(
          cfg.cookies.map((c) => {
            const obj = c as { name?: unknown; value?: unknown }
            return {
              name: typeof obj.name === 'string' ? obj.name : '',
              value: typeof obj.value === 'string' ? obj.value : '',
            }
          }),
        )
      }
      // SPEC D-1: requiresLogin is pre-set only for ?step=auth deep-link
      // (handled above). Otherwise null so the auth-question step always runs.
      if (cfg.content_selector) setShowAdvancedSelector(true)
      setWcPreviewUrl(String(cfg.base_url ?? ''))
    }
    if (connector.connector_type === 'github') {
      const cfg = connector.config as { installation_id?: number; repo_owner?: string; repo_name?: string; branch?: string; path_filter?: string }
      setGithubConfig({
        installation_id: String(cfg.installation_id ?? ''),
        repo_owner: String(cfg.repo_owner ?? ''),
        repo_name: String(cfg.repo_name ?? ''),
        branch: String(cfg.branch ?? 'main'),
        path_filter: String(cfg.path_filter ?? ''),
      })
    }
    if (connector.connector_type === 'notion') {
      const cfg = connector.config as { database_ids?: string[]; max_pages?: number }
      setNotionConfig({
        database_ids: (cfg.database_ids ?? []).join('\n'),
        max_pages: String(cfg.max_pages ?? '500'),
        new_access_token: '',
      })
    }
    if (connector.connector_type === 'google_drive') {
      const cfg = connector.config as { folder_id?: string }
      setFolderId(cfg.folder_id ?? '')
    }
    if (connector.connector_type === 'ms_docs') {
      const cfg = connector.config as { site_url?: string; drive_id?: string }
      setMsSiteUrl(cfg.site_url ?? '')
      setMsDriveId(cfg.drive_id ?? '')
      setMsSiteUrlError(null)
    }
    if (connector.connector_type === 'airtable') {
      const cfg = connector.config as { api_key?: string; base_id?: string; table_names?: string[]; view_name?: string }
      setAirtableConfig({
        api_key: String(cfg.api_key ?? ''),
        base_id: String(cfg.base_id ?? ''),
        table_names: (cfg.table_names ?? []).join(', '),
        view_name: String(cfg.view_name ?? ''),
      })
    }
    if (connector.connector_type === 'confluence') {
      const cfg = connector.config as { base_url?: string; email?: string; api_token?: string; space_keys?: string[] }
      setConfluenceConfig({
        base_url: String(cfg.base_url ?? ''),
        email: String(cfg.email ?? ''),
        api_token: '',  // never pre-populate secrets
        space_keys: (cfg.space_keys ?? []).join(', '),
      })
    }
  }, [connector?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!connector) return
      const config: Record<string, unknown> = {}
      if (connector.connector_type === 'github') {
        config.installation_id = Number(githubConfig.installation_id)
        config.repo_owner = githubConfig.repo_owner
        config.repo_name = githubConfig.repo_name
        config.branch = githubConfig.branch
        if (githubConfig.path_filter) config.path_filter = githubConfig.path_filter
      }
      if (connector.connector_type === 'web_crawler') {
        config.base_url = webcrawlerConfig.base_url
        if (webcrawlerConfig.path_prefix) config.path_prefix = webcrawlerConfig.path_prefix
        if (webcrawlerConfig.max_pages) config.max_pages = Number(webcrawlerConfig.max_pages)
        if (webcrawlerConfig.content_selector) config.content_selector = webcrawlerConfig.content_selector
        // SPEC-CRAWL-004: auth guard from ``authGuard`` state — initialized from
        // auth-probe at step 4 → 5 bridge, refreshed by preview onSuccess,
        // mutated by the operator-editable form on step 5.
        const ag = authGuard
        if (ag?.canary_url) {
          config.canary_url = ag.canary_url
          if (ag.canary_fingerprint) config.canary_fingerprint = ag.canary_fingerprint
        }
        if (ag?.login_indicator_selector) config.login_indicator_selector = ag.login_indicator_selector
        // Include cookies if entered (for both sync and canary fingerprint computation)
        const cookies = buildCookies()
        if (cookies) config.cookies = cookies
      }
      if (connector.connector_type === 'notion') {
        if (notionConfig.new_access_token.trim()) {
          config.access_token = notionConfig.new_access_token.trim()
        }
        const ids = notionConfig.database_ids.split('\n').map((s) => s.trim()).filter(Boolean)
        if (ids.length > 0) config.database_ids = ids
        if (notionConfig.max_pages) config.max_pages = Number(notionConfig.max_pages)
      }
      if (connector.connector_type === 'google_drive') {
        if (folderId.trim()) config.folder_id = folderId.trim()
      }
      if (connector.connector_type === 'ms_docs') {
        const siteUrl = msSiteUrl.trim()
        if (siteUrl && !MS_SITE_URL_PATTERN.test(siteUrl)) {
          setMsSiteUrlError(m.admin_connectors_ms_docs_site_url_invalid())
          throw new Error('invalid_site_url')
        }
        setMsSiteUrlError(null)
        if (siteUrl) config.site_url = siteUrl
        if (msDriveId.trim()) config.drive_id = msDriveId.trim()
      }
      if (connector.connector_type === 'airtable') {
        if (airtableConfig.api_key.trim()) config.api_key = airtableConfig.api_key.trim()
        config.base_id = airtableConfig.base_id
        config.table_names = airtableConfig.table_names
          .split(',').map((s) => s.trim()).filter(Boolean)
        if (airtableConfig.view_name.trim()) config.view_name = airtableConfig.view_name.trim()
      }
      if (connector.connector_type === 'confluence') {
        config.base_url = confluenceConfig.base_url.replace(/\/$/, '')
        config.email = confluenceConfig.email
        if (confluenceConfig.api_token.trim()) config.api_token = confluenceConfig.api_token.trim()
        const keys = confluenceConfig.space_keys.split(',').map((s) => s.trim()).filter(Boolean)
        if (keys.length > 0) config.space_keys = keys
      }
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${connectorId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name,
          config,
          allowed_assertion_modes: allowedAssertionModes.length > 0 ? allowedAssertionModes : null,
        }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.connectorsPortal(kbSlug) })
      goBack()
    },
  })

  // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-3 — edit wizard preview mutation.
  const previewMutation = useMutation({
    mutationFn: async ({ url, content_selector, try_ai, cookies }: { url: string; content_selector?: string; try_ai?: boolean; cookies?: unknown[] }) => {
      return apiFetch<PreviewResult>(`/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`, {
        method: 'POST',
        body: JSON.stringify({ url, content_selector: content_selector || null, try_ai: try_ai ?? false, cookies: cookies || null }),
      })
    },
    onSuccess: (data) => {
      setPreviewResult(data)
      setPreviewError(null)
      // Preview is the freshest signal of effective auth state, so let its
      // ``auth_guard`` overwrite whatever the auth-probe seeded earlier.
      setAuthGuard(data.auth_guard)
      // Auth-wall detected: redirect back to auth-setup
      if (data.classification === 'auth_wall_detected') {
        setRequiresLogin(true)
        setWcStep('auth-setup')
        setAuthProbeResult(null)
        return
      }
      if (data.classification === 'selector_required' || data.classification === 'selector_returns_empty') {
        setShowAdvancedSelector(true)
      }
    },
    onError: (err) => {
      setPreviewError(err instanceof Error ? err.message : m.admin_connectors_error_create_generic())
      setPreviewResult(null)
    },
  })

  // SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-2 — edit wizard auth probe.
  const authProbeMutation = useMutation({
    mutationFn: async ({ url, cookies }: { url: string; cookies?: unknown[] }) => {
      return apiFetch<AuthProbeResult>(`/api/app/knowledge-bases/${kbSlug}/connectors/auth-probe`, {
        method: 'POST',
        body: JSON.stringify({ url, cookies: cookies || null }),
      })
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

  // SPEC D-10: cache invalidation helpers
  function invalidateAuthProbe() {
    setAuthProbeResult(null)
    setAuthProbeError(null)
  }
  function invalidatePreview() {
    setPreviewResult(null)
    setPreviewError(null)
  }

  function renderError() {
    if (!updateMutation.error) return null
    return (
      <p className="text-sm text-[var(--color-destructive)]">
        {updateMutation.error instanceof Error ? updateMutation.error.message : m.admin_connectors_error_create_generic()}
      </p>
    )
  }

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <div className="space-y-1.5">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_connectors_edit_title()}
          </h1>
          {connector && (
            <p className="text-sm text-gray-400">{connector.name}</p>
          )}
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_cancel()}
        </Button>
      </div>

          {/* Web crawler — 5-step wizard (mirrors add-connector, edit-specific differences per SPEC D-1 / D-2) */}
          {connector?.connector_type === 'web_crawler' && (
            <div className="space-y-4">
              {/* Step indicator */}
              {(() => {
                const steps: StepItem[] = [
                  { label: m.admin_connectors_webcrawler_step_details(), onClick: () => setWcStep('details') },
                  { label: 'Authentication', onClick: () => setWcStep('auth-question') },
                  { label: m.admin_connectors_webcrawler_step_preview(), onClick: () => setWcStep('selector') },
                  { label: m.admin_connectors_webcrawler_step_settings() },
                ]
                const WC_STEP_INDEX: Record<WcStep, number> = {
                  details: 0,
                  'auth-question': 1,
                  'auth-setup': 1,
                  selector: 2,
                  settings: 3,
                }
                return <StepIndicator steps={steps} currentIndex={WC_STEP_INDEX[wcStep]} />
              })()}

              {/* Step 1: Details */}
              {wcStep === 'details' && (
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-wc-name">{m.admin_connectors_field_name()}</Label>
                    <Input id="edit-wc-name" required value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-wc-base-url">{m.admin_connectors_webcrawler_base_url()}</Label>
                    <Input
                      id="edit-wc-base-url"
                      type="url"
                      required
                      value={webcrawlerConfig.base_url}
                      onChange={(e) => {
                        setWebcrawlerConfig((p) => ({ ...p, base_url: e.target.value }))
                        invalidateAuthProbe()
                        invalidatePreview()
                      }}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-wc-path-prefix">{m.admin_connectors_webcrawler_path_prefix()}</Label>
                    <Input
                      id="edit-wc-path-prefix"
                      value={webcrawlerConfig.path_prefix}
                      onChange={(e) => {
                        setWebcrawlerConfig((p) => ({ ...p, path_prefix: e.target.value }))
                        invalidateAuthProbe()
                        invalidatePreview()
                      }}
                    />
                  </div>
                  <div className="flex gap-2 pt-1">
                    <Button
                      type="button"
                      size="sm"
                      disabled={!name || !webcrawlerConfig.base_url}
                      onClick={() => {
                        setWcPreviewUrl(webcrawlerConfig.base_url)
                        setWcStep('auth-question')
                      }}
                    >
                      {m.admin_connectors_webcrawler_next()}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={goBack}>
                      {m.admin_connectors_cancel()}
                    </Button>
                  </div>
                </div>
              )}

              {/* Step 2: Authentication question */}
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
                      onClick={() => setWcStep(requiresLogin ? 'auth-setup' : 'selector')}
                    >
                      {m.admin_connectors_webcrawler_next()}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => setWcStep('details')}>
                      {m.admin_connectors_webcrawler_back()}
                    </Button>
                  </div>
                </div>
              )}

              {/* Step 3: Auth setup — REQ-2 auth-probe */}
              {wcStep === 'auth-setup' && (
                <div className="space-y-4">
                  <div className="rounded-lg border border-gray-200 p-4 space-y-3">
                    <p className="text-sm font-medium text-gray-900">
                      Authentication cookies
                    </p>
                    <CookieRowsInput
                      idPrefix="edit-wc-cookie"
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
                        // Carry auth_guard forward in its own state slot — selector step
                        // (5) starts EMPTY (previewResult stays null) so no amber
                        // "Preview service did not respond" renders before the operator
                        // has clicked Run preview. Save reads ``authGuard`` directly.
                        setAuthGuard(authProbeResult?.auth_guard ?? null)
                        setWcStep('selector')
                      }}
                    >
                      {m.admin_connectors_webcrawler_next()}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => setWcStep('auth-question')}>
                      {m.admin_connectors_webcrawler_back()}
                    </Button>
                  </div>
                </div>
              )}

              {/* Step 4: Selector — REQ-3 preview, gates save on classification === success */}
              {wcStep === 'selector' && (
                <div className="space-y-4">
                  {/* Auth status reminder */}
                  {requiresLogin === false && (
                    <div className="flex items-center justify-between rounded-lg border border-gray-200 px-4 py-3">
                      <div className="flex items-center gap-2 text-xs text-gray-400">
                        <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-success)]" />
                        Public site — no login needed
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
                        Logged in — cookies verified
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
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-wc-preview-url">{m.admin_connectors_webcrawler_preview_url()}</Label>
                    <Input
                      id="edit-wc-preview-url"
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
                        id="edit-wc-content-selector"
                        placeholder={m.admin_connectors_webcrawler_content_selector_placeholder()}
                        value={webcrawlerConfig.content_selector}
                        onChange={(e) => {
                          setWebcrawlerConfig((p) => ({ ...p, content_selector: e.target.value }))
                          invalidatePreview()
                        }}
                      />
                      <p className="text-xs text-gray-400">
                        Only needed if the preview picks up menus instead of the article.
                        Leave empty to let AI detect this automatically.
                      </p>
                    </div>
                  )}

                  {/* Run preview button */}
                  {/* Run preview + AI-find — same upfront affordance as add-connector. */}
                  <div className="flex flex-wrap gap-2 items-center">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={previewMutation.isPending || !wcPreviewUrl}
                      onClick={() => {
                        invalidatePreview()
                        previewMutation.mutate({
                          url: wcPreviewUrl,
                          content_selector: webcrawlerConfig.content_selector,
                          cookies: buildCookies(),
                        })
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
                          previewMutation.mutate({
                            url: wcPreviewUrl,
                            try_ai: true,
                            cookies: buildCookies(),
                          })
                        }}
                      >
                        <Sparkles className="h-3 w-3" />
                        {m.admin_connectors_webcrawler_try_ai()}
                      </button>
                    )}
                  </div>

                  {/* Preview feedback — classification-driven single source of truth */}
                  {previewError && !previewMutation.isPending && (
                    <p className="text-sm text-[var(--color-destructive)]">{previewError}</p>
                  )}
                  {previewMutation.isPending && (
                    <div className="rounded-lg border border-gray-200 p-4 flex items-center gap-2 text-sm text-gray-400">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {m.admin_connectors_webcrawler_preview_loading()}
                    </div>
                  )}
                  {!previewResult && !previewMutation.isPending && !previewError && (
                    <p className="text-sm text-gray-400">{m.admin_connectors_webcrawler_preview_empty()}</p>
                  )}
                  {previewResult !== null && !previewMutation.isPending && (
                    <div className="space-y-3">
                      <PreviewClassificationFeedback
                        classification={previewResult.classification}
                        reason={previewResult.classification_reason}
                        onRetry={() => {
                          invalidatePreview()
                          previewMutation.mutate({
                            url: wcPreviewUrl,
                            content_selector: webcrawlerConfig.content_selector,
                            cookies: buildCookies(),
                          })
                        }}
                      />

                      {/* AI-detected selector badge + "Use this selector" CTA */}
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

                      {/* Inline "Try AI find selector" CTA for applicable classifications */}
                      {(previewResult.classification === 'selector_required' || previewResult.classification === 'selector_returns_empty') &&
                        !webcrawlerConfig.content_selector && previewResult.selector_source !== 'ai' && (
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

                      {/* Extracted markdown body — success only */}
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

                      {/* Auth guard confirmation block — only after a successful
                          preview AND when we have an auth_guard to work with
                          (either fresh from preview or carried over from the
                          auth-probe). Source of truth is ``authGuard`` state. */}
                      {previewResult.classification === 'success' && authGuard?.canary_url && (
                        <div className="rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 space-y-2">
                          <div className="flex gap-2 items-center text-xs text-[var(--color-success)]">
                            <Shield className="h-3.5 w-3.5 shrink-0" />
                            <span>Auth protection enabled</span>
                          </div>
                          <p className="text-xs text-gray-400 ml-5.5">
                            We&apos;ll check this page before every sync to detect expired logins.
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
                      disabled={previewResult?.classification !== 'success'}
                      onClick={() => setWcStep('settings')}
                    >
                      {m.admin_connectors_webcrawler_next()}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => setWcStep(requiresLogin ? 'auth-setup' : 'auth-question')}
                    >
                      {m.admin_connectors_webcrawler_back()}
                    </Button>
                  </div>
                </div>
              )}

              {/* Step 5: Settings + Save */}
              {wcStep === 'settings' && (
                <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-wc-max-pages">{m.admin_connectors_webcrawler_max_pages()}</Label>
                    <Input id="edit-wc-max-pages" type="number" min="1" max="2000" value={webcrawlerConfig.max_pages} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, max_pages: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                    <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                  </div>
                  {renderError()}
                  <div className="flex gap-2 pt-1">
                    <Button
                      type="submit"
                      size="sm"
                      disabled={
                        updateMutation.isPending ||
                        previewResult?.classification !== 'success' ||
                        (requiresLogin === true && authProbeResult?.classification !== 'auth_ok')
                      }
                    >
                      {updateMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_save()}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => setWcStep('selector')}>
                      {m.admin_connectors_webcrawler_back()}
                    </Button>
                  </div>
                </form>
              )}
            </div>
          )}

          {/* GitHub */}
          {connector?.connector_type === 'github' && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-install">{m.admin_connectors_github_installation_id()}</Label>
                <Input id="edit-conn-install" type="number" required value={githubConfig.installation_id} onChange={(e) => setGithubConfig((p) => ({ ...p, installation_id: e.target.value }))} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="edit-conn-owner">{m.admin_connectors_github_repo_owner()}</Label>
                  <Input id="edit-conn-owner" required value={githubConfig.repo_owner} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_owner: e.target.value }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="edit-conn-repo">{m.admin_connectors_github_repo_name()}</Label>
                  <Input id="edit-conn-repo" required value={githubConfig.repo_name} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_name: e.target.value }))} />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-branch">{m.admin_connectors_github_branch()}</Label>
                <Input id="edit-conn-branch" required value={githubConfig.branch} onChange={(e) => setGithubConfig((p) => ({ ...p, branch: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
              </div>
            </form>
          )}

          {/* Notion */}
          {connector?.connector_type === 'notion' && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-notion-token">{m.admin_connectors_notion_access_token()}</Label>
                <Input
                  id="edit-conn-notion-token"
                  type="password"
                  placeholder={m.admin_connectors_notion_access_token_placeholder()}
                  value={notionConfig.new_access_token}
                  onChange={(e) => setNotionConfig((p) => ({ ...p, new_access_token: e.target.value }))}
                />
                <p className="text-xs text-gray-400">{m.admin_connectors_notion_token_help_update()}</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-notion-dbs">{m.admin_connectors_notion_database_ids()}</Label>
                <textarea
                  id="edit-conn-notion-dbs"
                  rows={3}
                  placeholder={m.admin_connectors_notion_database_ids_placeholder()}
                  value={notionConfig.database_ids}
                  onChange={(e) => setNotionConfig((p) => ({ ...p, database_ids: e.target.value }))}
                  className="w-full rounded-lg border border-gray-200 bg-[var(--color-input)] px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] resize-none"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-notion-max-pages">{m.admin_connectors_notion_max_pages()}</Label>
                <Input
                  id="edit-conn-notion-max-pages"
                  type="number"
                  min="1"
                  max="5000"
                  value={notionConfig.max_pages}
                  onChange={(e) => setNotionConfig((p) => ({ ...p, max_pages: e.target.value }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
              </div>
            </form>
          )}

          {/* Google Drive */}
          {connector?.connector_type === 'google_drive' && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-folder-id">{m.admin_connectors_google_drive_folder_id()}</Label>
                <Input id="edit-conn-folder-id" placeholder={m.admin_connectors_google_drive_folder_id_placeholder()} value={folderId} onChange={(e) => setFolderId(e.target.value)} />
                <p className="text-xs text-gray-400">{m.admin_connectors_google_drive_folder_id_help()}</p>
              </div>
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="flex gap-2 pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={isReconnecting}
                  onClick={() => { void handleGoogleDriveReconnect() }}
                >
                  {m.admin_connectors_google_drive_reconnect()}
                </Button>
              </div>
            </form>
          )}

          {/* Microsoft 365 (SPEC-KB-MS-DOCS-001 R4.4) */}
          {connector?.connector_type === 'ms_docs' && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-ms-site-url">{m.admin_connectors_ms_docs_site_url()}</Label>
                <Input
                  id="edit-ms-site-url"
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
                <Label htmlFor="edit-ms-drive-id">{m.admin_connectors_ms_docs_drive_id()}</Label>
                <Input id="edit-ms-drive-id" placeholder="b!xyz..." value={msDriveId} onChange={(e) => setMsDriveId(e.target.value)} />
                <p className="text-xs text-gray-400">{m.admin_connectors_ms_docs_drive_id_help()}</p>
              </div>
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="flex gap-2 pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={isReconnecting}
                  onClick={() => { void handleMsDocsReconnect() }}
                >
                  {m.admin_connectors_ms_docs_reconnect()}
                </Button>
              </div>
            </form>
          )}

          {/* Google Docs / Sheets / Slides — reuse Google Drive OAuth status */}
          {connector && ['google_docs', 'google_sheets', 'google_slides'].includes(connector.connector_type) && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              {connector.connector_type === 'google_docs' && (
                <p className="text-sm text-gray-400">{m.admin_connectors_google_docs_subtitle()}</p>
              )}
              {connector.connector_type === 'google_sheets' && (
                <p className="text-sm text-gray-400">{m.admin_connectors_google_sheets_subtitle()}</p>
              )}
              {connector.connector_type === 'google_slides' && (
                <p className="text-sm text-gray-400">{m.admin_connectors_google_slides_subtitle()}</p>
              )}
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="flex gap-2 pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={isReconnecting}
                  onClick={() => { void handleGoogleDriveReconnect() }}
                >
                  {m.admin_connectors_google_drive_reconnect()}
                </Button>
              </div>
            </form>
          )}

          {/* Airtable (SPEC-KB-CONNECTORS-001 R3) */}
          {connector?.connector_type === 'airtable' && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-at-api-key">{m.admin_connectors_airtable_api_key_label()}</Label>
                <Input id="edit-at-api-key" type="password" placeholder={m.admin_connectors_airtable_api_key_hint()} value={airtableConfig.api_key} onChange={(e) => setAirtableConfig((p) => ({ ...p, api_key: e.target.value }))} />
                <p className="text-xs text-gray-400">{m.admin_connectors_notion_token_help_update()}</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-at-base-id">{m.admin_connectors_airtable_base_id_label()}</Label>
                <Input id="edit-at-base-id" required placeholder={m.admin_connectors_airtable_base_id_hint()} value={airtableConfig.base_id} onChange={(e) => setAirtableConfig((p) => ({ ...p, base_id: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-at-tables">{m.admin_connectors_airtable_table_names_label()}</Label>
                <Input id="edit-at-tables" required placeholder={m.admin_connectors_airtable_table_names_hint()} value={airtableConfig.table_names} onChange={(e) => setAirtableConfig((p) => ({ ...p, table_names: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-at-view">{m.admin_connectors_airtable_view_name_label()}</Label>
                <Input id="edit-at-view" placeholder={m.admin_connectors_airtable_view_name_hint()} value={airtableConfig.view_name} onChange={(e) => setAirtableConfig((p) => ({ ...p, view_name: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
              </div>
            </form>
          )}

          {/* Confluence (SPEC-KB-CONNECTORS-001 R4) */}
          {connector?.connector_type === 'confluence' && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-cf-base-url">{m.admin_connectors_confluence_base_url_label()}</Label>
                <Input id="edit-cf-base-url" type="url" required placeholder={m.admin_connectors_confluence_base_url_hint()} value={confluenceConfig.base_url} onChange={(e) => setConfluenceConfig((p) => ({ ...p, base_url: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-cf-email">{m.admin_connectors_confluence_email_label()}</Label>
                <Input id="edit-cf-email" type="email" required placeholder="you@company.com" value={confluenceConfig.email} onChange={(e) => setConfluenceConfig((p) => ({ ...p, email: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-cf-token">{m.admin_connectors_confluence_api_token_label()}</Label>
                <Input id="edit-cf-token" type="password" placeholder={m.admin_connectors_notion_token_help_update()} value={confluenceConfig.api_token} onChange={(e) => setConfluenceConfig((p) => ({ ...p, api_token: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-cf-spaces">{m.admin_connectors_confluence_space_keys_label()}</Label>
                <Input id="edit-cf-spaces" placeholder={m.admin_connectors_confluence_space_keys_hint()} value={confluenceConfig.space_keys} onChange={(e) => setConfluenceConfig((p) => ({ ...p, space_keys: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
              </div>
            </form>
          )}

          {/* Generic fallback for unsupported connector types */}
          {connector && !['web_crawler', 'github', 'notion', 'google_drive', 'ms_docs', 'airtable', 'confluence', 'google_docs', 'google_sheets', 'google_slides'].includes(connector.connector_type) && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
              </div>
              {renderError()}
              <div className="pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
              </div>
            </form>
          )}

          {!connector && (
            <p className="text-sm text-gray-400">{m.admin_connectors_loading()}</p>
          )}
    </div>
  )
}
