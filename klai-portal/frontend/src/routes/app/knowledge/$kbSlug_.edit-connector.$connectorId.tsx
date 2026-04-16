import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, AlertTriangle, Globe, FileText,
  Settings, ChevronDown, ChevronRight, CheckCircle2, Loader2, Sparkles,
} from 'lucide-react'
import { SiGithub, SiNotion, SiGoogledrive } from '@icons-pack/react-simple-icons'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { ASSERTION_MODE_OPTIONS } from './$kbSlug/-kb-helpers'
import type { ConnectorSummary, GitHubConfig, WebCrawlerConfig } from './$kbSlug/-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug_/edit-connector/$connectorId')({
  component: EditConnectorPage,
})

interface NotionEditConfig {
  database_ids: string
  max_pages: string
  new_access_token: string
}

type PreviewResult = {
  fit_markdown: string
  word_count: number
  warnings: string[]
  content_selector: string | null
  selector_source: string | null
}

const MARKDOWN_PROSE_CLASSES = 'overflow-y-auto max-h-64 text-xs [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-[var(--color-foreground)] [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-[var(--color-foreground)] [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-[var(--color-foreground)] [&_h3]:mb-1 [&_p]:text-[var(--color-muted-foreground)] [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-[var(--color-muted-foreground)] [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-[var(--color-muted-foreground)] [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-[var(--color-foreground)] [&_hr]:border-[var(--color-border)] [&_hr]:my-2'

type ConnectorTypeInfo = { label: string; Icon: React.ComponentType<{ className?: string }> }
const CONNECTOR_TYPE_MAP: Record<string, ConnectorTypeInfo> = {
  github:       { label: 'GitHub',       Icon: SiGithub },
  web_crawler:  { label: 'Web',          Icon: Globe },
  notion:       { label: 'Notion',       Icon: SiNotion },
  google_drive: { label: 'Google Drive', Icon: SiGoogledrive },
  ms_docs:      { label: 'MS Docs',      Icon: FileText },
}

function EditConnectorPage() {
  const { kbSlug, connectorId } = Route.useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const token = user?.access_token
  const queryClient = useQueryClient()

  function goBack() {
    void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug }, search: { tab: 'connectors' } })
  }

  const { data: connectors = [] } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kbSlug],
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, token),
    enabled: !!token,
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

  // Web crawler preview state
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [wcPreviewUrl, setWcPreviewUrl] = useState('')
  const [wcCookies, setWcCookies] = useState('')
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)

  async function handleGoogleDriveReconnect() {
    setIsReconnecting(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        `/api/oauth/google_drive/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(connectorId)}`,
        token,
      )
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
      const cfg = connector.config as { base_url?: string; path_prefix?: string; max_pages?: number; content_selector?: string }
      setWebcrawlerConfig({
        base_url: String(cfg.base_url ?? ''),
        path_prefix: String(cfg.path_prefix ?? ''),
        max_pages: String(cfg.max_pages ?? '200'),
        content_selector: cfg.content_selector ?? '',
      })
      // Auto-expand Advanced if a selector was previously saved
      if (cfg.content_selector) setShowAdvanced(true)
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
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${connectorId}`, token, {
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
      return apiFetch<PreviewResult>(
        `/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`,
        token,
        { method: 'POST', body: JSON.stringify({ url, content_selector: content_selector || null, try_ai: try_ai ?? false, cookies: cookies ?? null }) },
      )
    },
    onSuccess: (data) => {
      setPreviewResult(data)
      setPreviewError(null)
      if (data.selector_source === 'ai' && data.content_selector) {
        setWebcrawlerConfig((p) => ({ ...p, content_selector: data.content_selector! }))
        setShowAdvanced(true)
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
    <div className="p-6 max-w-lg">
      <div className="flex items-start justify-between mb-6">
        <div className="space-y-1.5">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {m.admin_connectors_edit_title()}
          </h1>
          {connector && (() => {
            const info = CONNECTOR_TYPE_MAP[connector.connector_type]
            const Icon = info?.Icon ?? FileText
            const label = info?.label ?? connector.connector_type
            return (
              <div className="flex items-center gap-1.5">
                <Icon className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                <Badge variant="secondary" className="text-xs font-normal">{label}</Badge>
              </div>
            )
          })()}
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">

          {/* Web crawler */}
          {connector?.connector_type === 'web_crawler' && (
            <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate() }} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                <Input id="edit-conn-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-base-url">{m.admin_connectors_webcrawler_base_url()}</Label>
                <Input id="edit-conn-base-url" type="url" required value={webcrawlerConfig.base_url} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, base_url: e.target.value }))} />
              </div>

              {/* Preview URL + Advanced toggle (mirrors add-connector preview step) */}
              <div className="space-y-1.5">
                <Label htmlFor="edit-conn-preview-url">{m.admin_connectors_webcrawler_preview_url()}</Label>
                <Input
                  id="edit-conn-preview-url"
                  type="url"
                  placeholder={webcrawlerConfig.base_url}
                  value={wcPreviewUrl}
                  onChange={(e) => setWcPreviewUrl(e.target.value)}
                />
              </div>
              <button
                type="button"
                className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors"
                onClick={() => setShowAdvanced((p) => !p)}
              >
                <Settings className="h-3 w-3" />
                {m.admin_connectors_webcrawler_advanced_toggle()}
                {showAdvanced ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              </button>
              {showAdvanced && (
                <div className="pl-4 border-l-2 border-[var(--color-border)] space-y-3">
                  <Input
                    id="edit-conn-content-selector"
                    placeholder={m.admin_connectors_webcrawler_content_selector_placeholder()}
                    value={webcrawlerConfig.content_selector}
                    onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, content_selector: e.target.value }))}
                  />
                  <div className="space-y-1.5">
                    <Label htmlFor="edit-conn-cookies">{m.admin_connectors_webcrawler_cookies_label()}</Label>
                    <textarea
                      id="edit-conn-cookies"
                      className="flex min-h-[60px] w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input)] px-3 py-2 text-xs font-mono placeholder:text-[var(--color-muted-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
                      placeholder={m.admin_connectors_webcrawler_cookies_placeholder()}
                      value={wcCookies}
                      onChange={(e) => setWcCookies(e.target.value)}
                    />
                    <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_cookies_help()}</p>
                  </div>
                </div>
              )}
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
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={previewMutation.isPending || !webcrawlerConfig.base_url}
                onClick={() => runPreview()}
              >
                {previewMutation.isPending
                  ? <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />{m.admin_connectors_webcrawler_preview_loading()}</>
                  : m.admin_connectors_webcrawler_run_preview()
                }
              </Button>
              {previewError && !previewMutation.isPending && (
                <p className="text-sm text-[var(--color-destructive)]">{previewError}</p>
              )}
              {previewMutation.isPending && (
                <div className="rounded-lg border border-[var(--color-border)] p-4 flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {m.admin_connectors_webcrawler_preview_loading()}
                </div>
              )}
              {previewResult !== null && !previewMutation.isPending && (previewResult.warnings ?? []).length === 0 && previewResult.word_count > 0 && (
                <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 text-xs text-[var(--color-success)]">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  <span>{m.admin_connectors_webcrawler_preview_looks_good({ count: String(previewResult.word_count) })}</span>
                </div>
              )}
              {previewResult !== null && !previewMutation.isPending && (previewResult.warnings ?? []).length > 0 && (
                <div className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                  <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  <span>{m.admin_connectors_webcrawler_warning_nav_detected()}</span>
                </div>
              )}
              {previewResult !== null && !previewMutation.isPending && previewResult.word_count === 0 && previewResult.selector_source !== 'ai' && (
                <div className="space-y-2">
                  <div className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                    <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    <span>{m.admin_connectors_webcrawler_preview_no_content()}</span>
                  </div>
                  {!webcrawlerConfig.content_selector && (
                    <button
                      type="button"
                      className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-accent)] transition-colors"
                      onClick={() => runPreview({ try_ai: true })}
                    >
                      <Sparkles className="h-3 w-3" />
                      {m.admin_connectors_webcrawler_try_ai()}
                    </button>
                  )}
                </div>
              )}
              {previewResult !== null && !previewMutation.isPending && previewResult.selector_source === 'ai' && previewResult.content_selector && (
                <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 space-y-1.5">
                  <p className="text-xs font-medium text-[var(--color-foreground)]">{m.admin_connectors_webcrawler_ai_selector_found()}</p>
                  <code className="text-xs text-[var(--color-accent-dark)] font-mono">{previewResult.content_selector}</code>
                </div>
              )}
              {previewResult !== null && !previewMutation.isPending && previewResult.fit_markdown && (
                <div className="rounded-lg border border-[var(--color-border)] p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-[var(--color-foreground)]">{m.admin_connectors_webcrawler_preview_title()}</span>
                    <span className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_word_count({ count: String(previewResult.word_count) })}</span>
                  </div>
                  <div className={MARKDOWN_PROSE_CLASSES}>
                    <ReactMarkdown components={{ a: ({ children }) => <span className="text-[var(--color-accent)]">{children}</span> }}>{previewResult.fit_markdown}</ReactMarkdown>
                  </div>
                </div>
              )}

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
              {renderError()}
              <div className="pt-2">
                <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
              </div>
            </form>
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

          {/* Generic fallback for unsupported connector types */}
          {connector && !['web_crawler', 'github', 'notion', 'google_drive'].includes(connector.connector_type) && (
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
        </CardContent>
      </Card>
    </div>
  )
}
