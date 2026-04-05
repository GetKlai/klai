import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  ArrowLeft, ChevronRight, Settings, ChevronDown, AlertTriangle, CheckCircle2, Loader2, Sparkles,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect, type MultiSelectOption } from '@/components/ui/multi-select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

// -- Types -------------------------------------------------------------------

type ConnectorType = 'github' | 'web_crawler' | 'google_drive' | 'notion' | 'ms_docs'
type WcStep = 'details' | 'preview' | 'settings'

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

const ASSERTION_MODE_OPTIONS: MultiSelectOption[] = [
  { value: 'factual',     label: 'Fact',        description: 'Established fact, documentation, specs' },
  { value: 'procedural',  label: 'Procedure',   description: "Step-by-step instructions, how-to's" },
  { value: 'belief',      label: 'Claim',       description: 'Not conclusively proven claim' },
  { value: 'quoted',      label: 'Quote',       description: 'Literal source material' },
  { value: 'hypothesis',  label: 'Speculation', description: 'Hypotheses, brainstorm' },
  { value: 'unknown',     label: 'Unknown',     description: 'Type not specified' },
]

const CONNECTOR_TYPES: { type: ConnectorType; label: () => string; available: boolean }[] = [
  { type: 'github', label: m.admin_connectors_type_github, available: true },
  { type: 'web_crawler', label: m.admin_connectors_type_website, available: true },
  { type: 'google_drive', label: m.admin_connectors_type_google_drive, available: false },
  { type: 'notion', label: m.admin_connectors_type_notion, available: true },
  { type: 'ms_docs', label: m.admin_connectors_type_ms_docs, available: false },
]

const MARKDOWN_PROSE_CLASSES = 'overflow-y-auto max-h-64 text-xs [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-[var(--color-purple-deep)] [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-[var(--color-purple-deep)] [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-[var(--color-purple-deep)] [&_h3]:mb-1 [&_p]:text-[var(--color-muted-foreground)] [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-[var(--color-muted-foreground)] [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-[var(--color-muted-foreground)] [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-[var(--color-purple-deep)] [&_hr]:border-[var(--color-border)] [&_hr]:my-2'

// -- Route -------------------------------------------------------------------

export const Route = createFileRoute('/app/knowledge/$kbSlug_/add-connector')({
  component: AddConnectorPage,
})

// -- Component ---------------------------------------------------------------

function AddConnectorPage() {
  const { kbSlug } = Route.useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const token = user?.access_token
  const queryClient = useQueryClient()

  const [selectedType, setSelectedType] = useState<ConnectorType | null>(null)
  const [name, setName] = useState('')
  const [allowedAssertionModes, setAllowedAssertionModes] = useState<string[]>([])
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '',
  })
  const [webcrawlerConfig, setWebcrawlerConfig] = useState<WebCrawlerConfig>({
    base_url: '', path_prefix: '', max_pages: '200', content_selector: '',
  })
  const [notionConfig, setNotionConfig] = useState<NotionConfig>({
    access_token: '', database_ids: '', max_pages: '500',
  })
  const [notionStep, setNotionStep] = useState<'credentials' | 'settings'>('credentials')

  // Webcrawler wizard state
  const [wcStep, setWcStep] = useState<WcStep>('details')
  const [showAdvancedSelector, setShowAdvancedSelector] = useState(false)
  const [wcPreviewUrl, setWcPreviewUrl] = useState('')
  const [previewResult, setPreviewResult] = useState<{
    fit_markdown: string; word_count: number; warnings: string[]
    content_selector: string | null; selector_source: string | null
  } | null>(null)

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
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/`, token, {
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

  const [previewError, setPreviewError] = useState<string | null>(null)

  const previewMutation = useMutation({
    mutationFn: async ({ url, content_selector, try_ai }: { url: string; content_selector?: string; try_ai?: boolean }) => {
      return apiFetch<{
        fit_markdown: string; word_count: number; warnings: string[]; url: string
        content_selector: string | null; selector_source: string | null
      }>(`/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`, token, {
        method: 'POST',
        body: JSON.stringify({ url, content_selector: content_selector || null, try_ai: try_ai ?? false }),
      })
    },
    onSuccess: (data) => {
      setPreviewResult(data)
      setPreviewError(null)
      // Auto-expand CSS selector section when there are problems
      if (data.word_count === 0 || (data.warnings ?? []).length > 0) {
        setShowAdvancedSelector(true)
      }
    },
    onError: (err) => { setPreviewError(err instanceof Error ? err.message : 'Preview failed'); setPreviewResult(null) },
  })

  return (
    <div className="p-8 max-w-lg">
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_connectors_add_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_connectors_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <div className="space-y-4">

            {/* Unified step breadcrumb — always shows all steps */}
            {(() => {
              type StepKey = 'type' | 'details' | 'preview' | 'settings' | 'configure'
              const wcSteps: { key: StepKey; label: string; onClick?: () => void }[] = [
                { key: 'type',     label: `1. ${m.admin_connectors_step_type()}`,                     onClick: () => setSelectedType(null) },
                { key: 'details',  label: `2. ${m.admin_connectors_webcrawler_step_details()}`,        onClick: () => setWcStep('details') },
                { key: 'preview',  label: `3. ${m.admin_connectors_webcrawler_step_preview()}`,        onClick: () => setWcStep('preview') },
                { key: 'settings', label: `4. ${m.admin_connectors_webcrawler_step_settings()}` },
              ]
              const ghSteps: { key: StepKey; label: string; onClick?: () => void }[] = [
                { key: 'type',      label: `1. ${m.admin_connectors_step_type()}`,      onClick: () => setSelectedType(null) },
                { key: 'configure', label: `2. ${m.admin_connectors_step_configure()}` },
              ]
              const notionSteps: { key: StepKey; label: string; onClick?: () => void }[] = [
                { key: 'type',      label: `1. ${m.admin_connectors_step_type()}`,      onClick: () => setSelectedType(null) },
                { key: 'configure', label: `2. ${m.admin_connectors_step_configure()}` },
              ]
              const steps = selectedType === 'github' ? ghSteps : selectedType === 'notion' ? notionSteps : wcSteps
              const currentKey: StepKey = !selectedType ? 'type' : (selectedType === 'github' || selectedType === 'notion') ? 'configure' : wcStep
              const currentIdx = steps.findIndex((s) => s.key === currentKey)
              return (
                <div className="flex items-center gap-1.5 text-xs flex-wrap">
                  {steps.map((step, i) => {
                    const isPast = i < currentIdx
                    const isActive = step.key === currentKey
                    return (
                      <span key={step.key} className="flex items-center gap-1.5">
                        {i > 0 && <ChevronRight className="h-3 w-3 text-[var(--color-muted-foreground)]" />}
                        {isPast && step.onClick ? (
                          <button type="button" className="text-[var(--color-accent)] hover:underline" onClick={step.onClick}>
                            {step.label}
                          </button>
                        ) : (
                          <span className={isActive ? 'font-medium text-[var(--color-purple-deep)]' : 'text-[var(--color-muted-foreground)]'}>
                            {step.label}
                          </span>
                        )}
                      </span>
                    )
                  })}
                </div>
              )
            })()}

            {/* Step 1: Type selection */}
            {!selectedType && (
              <div className="grid grid-cols-2 gap-3">
                {CONNECTOR_TYPES.map(({ type, label, available }) => (
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
                      !available ? 'cursor-not-allowed opacity-50' : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50',
                    ].join(' ')}
                  >
                    <span className="text-sm font-medium text-[var(--color-purple-deep)]">{label()}</span>
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
                      <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_notion_token_help()}</p>
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
                          setPreviewResult(null)
                          setPreviewError(null)
                          setWcStep('preview')
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

                {/* Step 2: Preview — all inputs above the single run button */}
                {wcStep === 'preview' && (
                  <div className="space-y-3">
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
                    <button
                      type="button"
                      className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)] transition-colors"
                      onClick={() => setShowAdvancedSelector((p) => !p)}
                    >
                      <Settings className="h-3 w-3" />
                      {m.admin_connectors_webcrawler_advanced_toggle()}
                      {showAdvancedSelector ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                    </button>
                    {showAdvancedSelector && (
                      <div className="pl-4 border-l-2 border-[var(--color-border)]">
                        <Input
                          id="wc-preview-selector"
                          placeholder={m.admin_connectors_webcrawler_content_selector_placeholder()}
                          value={webcrawlerConfig.content_selector}
                          onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, content_selector: e.target.value }))}
                        />
                      </div>
                    )}
                    {!webcrawlerConfig.content_selector && (
                      <button
                        type="button"
                        className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-accent)] transition-colors disabled:opacity-50"
                        disabled={previewMutation.isPending || !wcPreviewUrl}
                        onClick={() => {
                          setPreviewResult(null)
                          setPreviewError(null)
                          previewMutation.mutate({ url: wcPreviewUrl, try_ai: true })
                        }}
                      >
                        <Sparkles className="h-3 w-3" />
                        {m.admin_connectors_webcrawler_try_ai()}
                      </button>
                    )}
                    {/* Single run preview button */}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={previewMutation.isPending || !wcPreviewUrl}
                      onClick={() => {
                        setPreviewResult(null)
                        setPreviewError(null)
                        previewMutation.mutate({ url: wcPreviewUrl, content_selector: webcrawlerConfig.content_selector })
                      }}
                    >
                      {previewMutation.isPending
                        ? <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />{m.admin_connectors_webcrawler_preview_loading()}</>
                        : m.admin_connectors_webcrawler_run_preview()
                      }
                    </Button>
                    {/* Error / Result */}
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
                            onClick={() => {
                              setPreviewResult(null)
                              setPreviewError(null)
                              previewMutation.mutate({ url: wcPreviewUrl, try_ai: true })
                            }}
                          >
                            <Sparkles className="h-3 w-3" />
                            {m.admin_connectors_webcrawler_try_ai()}
                          </button>
                        )}
                      </div>
                    )}
                    {previewResult !== null && !previewMutation.isPending && previewResult.selector_source === 'ai' && previewResult.content_selector && (
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
                    {!previewResult && !previewMutation.isPending && (
                      <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_empty()}</p>
                    )}
                    {previewResult !== null && !previewMutation.isPending && previewResult.word_count > 0 && (
                      <div className="rounded-lg border border-[var(--color-border)] p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium text-[var(--color-purple-deep)]">{m.admin_connectors_webcrawler_preview_title()}</span>
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
                    <div className="flex gap-2 pt-1">
                      <Button type="button" size="sm" onClick={() => setWcStep('settings')}>{m.admin_connectors_webcrawler_next()}</Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setWcStep('details')}>{m.admin_connectors_webcrawler_back()}</Button>
                    </div>
                  </div>
                )}

                {/* Step 3: Settings */}
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
                      <Button type="button" size="sm" variant="ghost" onClick={() => setWcStep('preview')}>{m.admin_connectors_webcrawler_back()}</Button>
                    </div>
                  </form>
                )}
              </div>
            )}

          </div>
        </CardContent>
      </Card>
    </div>
  )
}
