import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, AlertTriangle, Shield, Info, Eye,
  CheckCircle2, Loader2, Sparkles, Settings, ChevronDown, ChevronRight,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { MS_SITE_URL_PATTERN } from '@/lib/ms-docs'
import { ASSERTION_MODE_OPTIONS } from './$kbSlug/-kb-helpers'
import type { ConnectorSummary, GitHubConfig, WebCrawlerConfig } from './$kbSlug/-kb-types'

type WcTabId = 'details' | 'preview' | 'auth'
const VALID_WC_TABS = new Set<WcTabId>(['details', 'preview', 'auth'])

type EditSearch = { tab?: WcTabId }

export const Route = createFileRoute('/app/knowledge/$kbSlug_/edit-connector/$connectorId')({
  validateSearch: (search: Record<string, unknown>): EditSearch => ({
    tab: (VALID_WC_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as WcTabId)
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

type PreviewResult = {
  fit_markdown: string
  word_count: number
  warnings: string[]
  content_selector: string | null
  selector_source: string | null
}

const MARKDOWN_PROSE_CLASSES = 'overflow-y-auto max-h-64 text-xs [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-[var(--color-foreground)] [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-[var(--color-foreground)] [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-[var(--color-foreground)] [&_h3]:mb-1 [&_p]:text-[var(--color-muted-foreground)] [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-[var(--color-muted-foreground)] [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-[var(--color-muted-foreground)] [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-[var(--color-foreground)] [&_hr]:border-[var(--color-border)] [&_hr]:my-2'


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
    queryKey: ['kb-connectors-portal', kbSlug],
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

  // Web crawler preview state
  const [showAdvancedSelector, setShowAdvancedSelector] = useState(false)
  const [wcPreviewUrl, setWcPreviewUrl] = useState('')
  const [wcCookies, setWcCookies] = useState('')
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  // Auth guard state (SPEC-CRAWL-004)
  const [canaryUrl, setCanaryUrl] = useState('')
  const [loginIndicatorSelector, setLoginIndicatorSelector] = useState('')

  async function handleGoogleDriveReconnect() {
    setIsReconnecting(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/google_drive/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(connectorId)}`, )
      window.location.href = authorize_url
    } finally {
      setIsReconnecting(false)
    }
  }

  // SPEC-KB-MS-DOCS-001 R4.4 — trigger a fresh OAuth flow when refresh_token is invalid.
  async function handleMsDocsReconnect() {
    setIsReconnecting(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(`/api/oauth/ms_docs/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(connectorId)}`, )
      window.location.href = authorize_url
    } finally {
      setIsReconnecting(false)
    }
  }

  function parseCookies(): unknown[] | undefined {
    const raw = wcCookies.trim()
    if (!raw) return undefined
    if (raw.startsWith('[')) {
      try { const parsed = JSON.parse(raw); return Array.isArray(parsed) ? parsed : undefined } catch { return undefined }
    }
    const domain = (() => { try { return new URL(webcrawlerConfig.base_url).hostname } catch { return '' } })()
    return raw.split(';').map((pair) => {
      const [cookieName, ...rest] = pair.trim().split('=')
      return { name: cookieName.trim(), value: rest.join('='), domain, path: '/' }
    }).filter((c) => c.name && c.value)
  }

  useEffect(() => {
    if (!connector) return
    setName(connector.name)
    setAllowedAssertionModes(connector.allowed_assertion_modes ?? [])
    if (connector.connector_type === 'web_crawler') {
      const cfg = connector.config as {
        base_url?: string; path_prefix?: string; max_pages?: number; content_selector?: string
        canary_url?: string; login_indicator_selector?: string
      }
      setWebcrawlerConfig({
        base_url: String(cfg.base_url ?? ''),
        path_prefix: String(cfg.path_prefix ?? ''),
        max_pages: String(cfg.max_pages ?? '200'),
        content_selector: cfg.content_selector ?? '',
      })
      setCanaryUrl(cfg.canary_url ?? '')
      setLoginIndicatorSelector(cfg.login_indicator_selector ?? '')
      if (cfg.content_selector) setShowAdvancedSelector(true)
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
        // Auth guard (SPEC-CRAWL-004): canary_fingerprint auto-computed by backend
        if (canaryUrl) config.canary_url = canaryUrl
        if (loginIndicatorSelector) config.login_indicator_selector = loginIndicatorSelector
        // Include cookies if entered (for both sync and canary fingerprint computation)
        const cookies = parseCookies()
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
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      goBack()
    },
  })

  const previewMutation = useMutation({
    mutationFn: async ({ url, content_selector, try_ai, cookies }: { url: string; content_selector?: string; try_ai?: boolean; cookies?: unknown[] }) => {
      return apiFetch<PreviewResult>(`/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`, { method: 'POST', body: JSON.stringify({ url, content_selector: content_selector || null, try_ai: try_ai ?? false, cookies: cookies ?? null }) }, )
    },
    onSuccess: (data) => {
      setPreviewResult(data)
      setPreviewError(null)
      if (data.selector_source === 'ai' && data.content_selector) {
        setWebcrawlerConfig((p) => ({ ...p, content_selector: data.content_selector! }))
      }
    },
    onError: (err) => {
      setPreviewError(err instanceof Error ? err.message : m.admin_connectors_error_create_generic())
    },
  })

  function runPreview(opts: { try_ai?: boolean } = {}) {
    const url = wcPreviewUrl || webcrawlerConfig.base_url
    setPreviewResult(null)
    setPreviewError(null)
    previewMutation.mutate({
      url,
      content_selector: opts.try_ai ? undefined : webcrawlerConfig.content_selector,
      try_ai: opts.try_ai,
      cookies: parseCookies(),
    })
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
            <p className="text-sm text-[var(--color-muted-foreground)]">{connector.name}</p>
          )}
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_cancel()}
        </Button>
      </div>

          {/* Web crawler — tabbed layout (widget pattern: URL-driven, border-b underline) */}
          {connector?.connector_type === 'web_crawler' && (() => {
            const activeTab: WcTabId = search.tab ?? 'details'
            const wcTabs: { id: WcTabId; label: string; icon: React.ElementType }[] = [
              { id: 'details', label: 'Details', icon: Info },
              { id: 'preview', label: 'Preview', icon: Eye },
              { id: 'auth',    label: 'Authentication', icon: Shield },
            ]
            function setTab(tab: WcTabId) {
              // Update search param only — preserves current route + params.
              // Using from/search function form to satisfy TanStack Router's strict types
              // on this $kbSlug_ layout route (param name mismatch with explicit `to`).
              void navigate({
                from: Route.fullPath,
                search: () => ({ tab }),
              })
            }
            return (
              <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-6">
                <div className="border-b border-[var(--color-border)]">
                  <nav className="-mb-px flex gap-6">
                    {wcTabs.map(({ id: tabId, label, icon: TabIcon }) => {
                      const isActive = tabId === activeTab
                      return (
                        <button
                          key={tabId}
                          type="button"
                          onClick={() => setTab(tabId)}
                          className={[
                            'flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 transition-colors',
                            isActive
                              ? 'border-[var(--color-accent)] text-[var(--color-foreground)]'
                              : 'border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
                          ].join(' ')}
                        >
                          <TabIcon className="h-4 w-4" />
                          {label}
                        </button>
                      )
                    })}
                  </nav>
                </div>

                {/* ── Details ── */}
                {activeTab === 'details' && (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                      <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-base-url">{m.admin_connectors_webcrawler_base_url()}</Label>
                      <Input id="edit-conn-base-url" type="url" required value={webcrawlerConfig.base_url} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, base_url: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-path-prefix">{m.admin_connectors_webcrawler_path_prefix()}</Label>
                      <Input id="edit-conn-path-prefix" value={webcrawlerConfig.path_prefix} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, path_prefix: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-max-pages">{m.admin_connectors_webcrawler_max_pages()}</Label>
                      <Input id="edit-conn-max-pages" type="number" min="1" max="2000" value={webcrawlerConfig.max_pages} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, max_pages: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                      <MultiSelect options={ASSERTION_MODE_OPTIONS} value={allowedAssertionModes} onChange={setAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                    </div>
                  </div>
                )}

                {/* ── Preview (mirrors add-connector wizard step 3) ── */}
                {activeTab === 'preview' && (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-preview-url">{m.admin_connectors_webcrawler_preview_url()}</Label>
                      <Input id="edit-conn-preview-url" type="url" placeholder={webcrawlerConfig.base_url} value={wcPreviewUrl} onChange={(e) => setWcPreviewUrl(e.target.value)} />
                    </div>

                    {/* Content selector — advanced toggle (same as wizard) */}
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
                          id="edit-conn-content-selector"
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

                    {/* AI selector detection */}
                    {!webcrawlerConfig.content_selector && (
                      <button
                        type="button"
                        className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-accent)] transition-colors disabled:opacity-50"
                        disabled={previewMutation.isPending || !webcrawlerConfig.base_url}
                        onClick={() => runPreview({ try_ai: true })}
                      >
                        <Sparkles className="h-3 w-3" />
                        {m.admin_connectors_webcrawler_try_ai()}
                      </button>
                    )}

                    {/* Run preview */}
                    <Button type="button" size="sm" variant="outline" disabled={previewMutation.isPending || !webcrawlerConfig.base_url} onClick={() => runPreview()}>
                      {previewMutation.isPending ? <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />{m.admin_connectors_webcrawler_preview_loading()}</> : m.admin_connectors_webcrawler_run_preview()}
                    </Button>

                    {/* Results */}
                    {previewError && !previewMutation.isPending && <p className="text-sm text-[var(--color-destructive)]">{previewError}</p>}
                    {previewMutation.isPending && (
                      <div className="rounded-lg border border-[var(--color-border)] p-4 flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]"><Loader2 className="h-4 w-4 animate-spin" />{m.admin_connectors_webcrawler_preview_loading()}</div>
                    )}
                    {previewResult !== null && !previewMutation.isPending && (previewResult.warnings ?? []).length === 0 && previewResult.word_count > 0 && (
                      <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 text-xs text-[var(--color-success)]"><CheckCircle2 className="h-3.5 w-3.5 shrink-0" /><span>{m.admin_connectors_webcrawler_preview_looks_good({ count: String(previewResult.word_count) })}</span></div>
                    )}
                    {previewResult !== null && !previewMutation.isPending && (previewResult.warnings ?? []).length > 0 && (
                      <div className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800"><AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" /><span>{m.admin_connectors_webcrawler_warning_nav_detected()}</span></div>
                    )}
                    {previewResult !== null && !previewMutation.isPending && previewResult.word_count === 0 && previewResult.selector_source !== 'ai' && (
                      <div className="space-y-2">
                        <div className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800"><AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" /><span>{m.admin_connectors_webcrawler_preview_no_content()}</span></div>
                        {!webcrawlerConfig.content_selector && (
                          <button type="button" className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-accent)] transition-colors" onClick={() => runPreview({ try_ai: true })}>
                            <Sparkles className="h-3 w-3" />{m.admin_connectors_webcrawler_try_ai()}
                          </button>
                        )}
                      </div>
                    )}
                    {previewResult !== null && !previewMutation.isPending && previewResult.selector_source === 'ai' && previewResult.content_selector && (
                      <div className="rounded-lg border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/5 p-3 space-y-2">
                        <div className="flex gap-2 items-center text-xs text-[var(--color-accent)]"><Sparkles className="h-3.5 w-3.5 shrink-0" /><span>{m.admin_connectors_webcrawler_ai_selector_detected({ selector: previewResult.content_selector, count: String(previewResult.word_count) })}</span></div>
                        {webcrawlerConfig.content_selector !== previewResult.content_selector && (
                          <Button type="button" size="sm" variant="outline" className="text-xs h-7" onClick={() => { setWebcrawlerConfig((p) => ({ ...p, content_selector: previewResult.content_selector! })); setShowAdvancedSelector(true) }}>
                            {m.admin_connectors_webcrawler_ai_selector_use()}
                          </Button>
                        )}
                      </div>
                    )}
                    {previewResult !== null && !previewMutation.isPending && previewResult.word_count > 0 && (
                      <div className="rounded-lg border border-[var(--color-border)] p-3 space-y-2">
                        <div className="flex items-center justify-between"><span className="text-sm font-medium text-[var(--color-foreground)]">{m.admin_connectors_webcrawler_preview_title()}</span><span className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_word_count({ count: String(previewResult.word_count) })}</span></div>
                        <div className={MARKDOWN_PROSE_CLASSES}><ReactMarkdown components={{ a: ({ children }) => <span className="text-[var(--color-accent)]">{children}</span> }}>{previewResult.fit_markdown}</ReactMarkdown></div>
                      </div>
                    )}
                  </div>
                )}

                {/* ── Authentication ── */}
                {activeTab === 'auth' && (
                  <div className="space-y-4">
                    {!wcCookies && !canaryUrl && !loginIndicatorSelector && (
                      <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 px-4 py-3 text-xs text-[var(--color-success)]">
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                        No authentication configured — this connector treats the site as public.
                      </div>
                    )}
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-cookies">Authentication cookies</Label>
                      <textarea id="edit-conn-cookies" className="flex min-h-[80px] w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-xs font-mono placeholder:text-[var(--color-muted-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]" placeholder={m.admin_connectors_webcrawler_cookies_placeholder()} value={wcCookies} onChange={(e) => setWcCookies(e.target.value)} />
                      <p className="text-xs text-[var(--color-muted-foreground)]">Open the site in your browser, log in, then copy the Cookie value from developer tools (Network tab &rarr; any request &rarr; Cookie header).</p>
                    </div>
                    <div className="space-y-3 pt-3 border-t border-[var(--color-border)]">
                      <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-foreground)]"><Shield className="h-3.5 w-3.5" />Auth protection</div>
                      <p className="text-xs text-[var(--color-muted-foreground)]">Usually set up automatically during preview. Only change these if you know what you&apos;re doing.</p>
                      <div className="space-y-1.5">
                        <Label htmlFor="edit-conn-canary-url" className="text-xs">Reference page</Label>
                        <Input id="edit-conn-canary-url" className="text-xs" placeholder="https://wiki.example.com/a-known-article" value={canaryUrl} onChange={(e) => setCanaryUrl(e.target.value)} />
                        <p className="text-xs text-[var(--color-muted-foreground)]">Checked before every sync. If this page looks different, the sync stops.</p>
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="edit-conn-login-selector" className="text-xs">Login indicator</Label>
                        <Input id="edit-conn-login-selector" className="text-xs" placeholder=".user-menu, a[href*=logout]" value={loginIndicatorSelector} onChange={(e) => setLoginIndicatorSelector(e.target.value)} />
                        <p className="text-xs text-[var(--color-muted-foreground)]">Pages without this element are skipped as login walls.</p>
                      </div>
                    </div>
                  </div>
                )}

                {renderError()}
                <div className="pt-2">
                  <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
                </div>
              </form>
            )
          })()}

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
                <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_notion_token_help_update()}</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-notion-dbs">{m.admin_connectors_notion_database_ids()}</Label>
                <textarea
                  id="edit-conn-notion-dbs"
                  rows={3}
                  placeholder={m.admin_connectors_notion_database_ids_placeholder()}
                  value={notionConfig.database_ids}
                  onChange={(e) => setNotionConfig((p) => ({ ...p, database_ids: e.target.value }))}
                  className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)] resize-none"
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
                <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_google_drive_folder_id_help()}</p>
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
                <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_ms_docs_site_url_help()}</p>
                {msSiteUrlError && (
                  <p className="text-xs text-[var(--color-destructive)]">{msSiteUrlError}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-ms-drive-id">{m.admin_connectors_ms_docs_drive_id()}</Label>
                <Input id="edit-ms-drive-id" placeholder="b!xyz..." value={msDriveId} onChange={(e) => setMsDriveId(e.target.value)} />
                <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_ms_docs_drive_id_help()}</p>
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
                <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_google_docs_subtitle()}</p>
              )}
              {connector.connector_type === 'google_sheets' && (
                <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_google_sheets_subtitle()}</p>
              )}
              {connector.connector_type === 'google_slides' && (
                <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_google_slides_subtitle()}</p>
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
                <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_notion_token_help_update()}</p>
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
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
          )}
    </div>
  )
}
