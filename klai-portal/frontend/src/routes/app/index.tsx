import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { AlertTriangle, Loader2, RefreshCw, Shield } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown } from 'lucide-react'

import { apiFetch } from '@/lib/apiFetch'
import { useAuth } from '@/lib/auth'
import { chatKbLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

const STUCK_TIMEOUT_MS = 20_000

type Phase = 'health_check' | 'loading_iframe' | 'ready' | 'stuck' | 'error'

interface ChatHealth { healthy: boolean; reason: string | null }

interface KBPref {
  kb_retrieval_enabled: boolean
  kb_personal_enabled: boolean
  kb_slugs_filter: string[] | null
  kb_narrow: boolean
  kb_pref_version: number
  active_template_ids: number[] | null
}

interface OrgKB { slug: string; name: string }

interface Template {
  id: number
  name: string
  slug: string
  scope: string
}

interface Rule {
  id: number
  name: string
  slug: string
  scope: string
  rule_type: 'pii_block' | 'pii_redact' | 'keyword_block' | 'keyword_redact'
  is_active: boolean
}

function useChatBaseUrl(): string {
  return useMemo(() => {
    const { hostname } = window.location
    if (hostname === 'localhost') return 'http://localhost:3080'
    const [tenant, ...rest] = hostname.split('.')
    // Dev now has its own LibreChat (`librechat-dev`) on chat-dev.getklai.com
    // routed by the _dev Caddy tenant file. No more rewrite to getklai.
    return `https://chat-${tenant}.${rest.join('.')}`
  }, [])
}

function getIframeSrc(baseUrl: string): string {
  // Always start at /oauth/openid. LibreChat's root renders an empty /login
  // page if the LC session cookie is missing/expired — which happens after
  // Mongo resets, container restarts, or cookie expiry. Triggering OIDC
  // unconditionally lets Zitadel's silent SSO complete in <1s when the user
  // has a valid portal session, and shows a real login UI otherwise.
  return `${baseUrl}/oauth/openid`
}

function getErrorMessage(reason: string | null): string {
  switch (reason) {
    case 'not_provisioned': return m.chat_health_failed_not_provisioned()
    case 'provisioning_in_progress': return m.chat_health_failed_provisioning()
    case 'sso_expired': return m.chat_health_failed_sso_expired()
    default: return m.chat_health_failed_generic()
  }
}

export const Route = createFileRoute('/app/')({
  component: ChatHome,
})

function ChatHome() {
  const baseUrl = useChatBaseUrl()
  const auth = useAuth()

  const [phase, setPhase] = useState<Phase>('health_check')
  const [errorReason, setErrorReason] = useState<string | null>(null)
  const [iframeSrc, setIframeSrc] = useState<string | null>(null)
  const stuckTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearStuckTimer = useCallback(() => {
    if (stuckTimer.current) { clearTimeout(stuckTimer.current); stuckTimer.current = null }
  }, [])

  const runHealthCheck = useCallback(async () => {
    setPhase('health_check')
    setErrorReason(null)
    try {
      const health = await apiFetch<ChatHealth>('/api/app/chat-health')
      if (!health.healthy) {
        chatKbLogger.warn('Chat health check failed', { reason: health.reason })
        setPhase('error')
        setErrorReason(health.reason)
        return
      }
      setIframeSrc(getIframeSrc(baseUrl))
      setPhase('loading_iframe')
      clearStuckTimer()
      stuckTimer.current = setTimeout(() => {
        setPhase((c) => c === 'loading_iframe' ? 'stuck' : c)
      }, STUCK_TIMEOUT_MS)
    } catch (err) {
      chatKbLogger.error('Chat health check request failed', { err })
      setIframeSrc(getIframeSrc(baseUrl))
      setPhase('loading_iframe')
    }
  }, [baseUrl, clearStuckTimer])

  useEffect(() => { void runHealthCheck(); return clearStuckTimer }, [runHealthCheck, clearStuckTimer])

  const handleIframeLoad = useCallback(() => {
    clearStuckTimer()
    setPhase('ready')
  }, [clearStuckTimer])

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return
      if (event.data?.type === 'klai-sso-failed') {
        clearStuckTimer(); setPhase('error'); setErrorReason('sso_expired')
      }
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [clearStuckTimer])

  const handleRetry = useCallback(() => {
    void auth.signinRedirect({ returnTo: '/app' })
  }, [auth])

  const showOverlay = phase === 'health_check' || phase === 'loading_iframe'
  const showError = phase === 'error' || phase === 'stuck'

  return (
    <div className="flex h-full w-full flex-col" data-help-id="chat-page">
      {/* Config bar — Superdock style */}
      <ChatConfigBar />

      <div className="relative flex-1">
        {showOverlay && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-white">
            <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
            <span className="text-sm text-gray-400">{m.chat_health_loading()}</span>
          </div>
        )}
        {showError && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-4 bg-white">
            <AlertTriangle className="h-8 w-8 text-gray-400" />
            <div className="text-center">
              <p className="text-sm font-medium text-gray-900">
                {phase === 'stuck' ? m.chat_health_stuck_title() : m.chat_health_failed_title()}
              </p>
              <p className="mt-1 max-w-sm text-xs text-gray-400">
                {phase === 'stuck' ? m.chat_health_stuck_description() : getErrorMessage(errorReason)}
              </p>
            </div>
            <button type="button" onClick={handleRetry}
              className="flex items-center gap-2 rounded-full bg-gray-900 px-4 py-2 text-sm text-white transition-colors hover:bg-gray-800">
              <RefreshCw className="h-4 w-4" />{m.chat_health_retry()}
            </button>
          </div>
        )}
        {iframeSrc && (
          <iframe src={iframeSrc} onLoad={handleIframeLoad}
            className={`h-full w-full border-none transition-opacity duration-200 ${phase === 'ready' ? 'opacity-100' : 'opacity-0'}`}
            title="Chat" allow="clipboard-write; microphone; screen-wake-lock" />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Config bar above chat — collections + template pickers (Superdock style)
// ---------------------------------------------------------------------------

function ChatConfigBar() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [collOpen, setCollOpen] = useState(false)
  const [tmplOpen, setTmplOpen] = useState(false)

  const { data: pref } = useQuery<KBPref>({
    queryKey: ['kb-preference'],
    queryFn: async () => apiFetch<KBPref>('/api/app/account/kb-preference'),
  })

  const { data: kbsData } = useQuery<{ knowledge_bases: OrgKB[] }>({
    queryKey: ['all-kbs-for-bar'],
    queryFn: async () => apiFetch<{ knowledge_bases: OrgKB[] }>('/api/app/knowledge-bases'),
  })

  const { data: templatesData } = useQuery<Template[]>({
    queryKey: ['app-templates-for-bar'],
    queryFn: async () => apiFetch<Template[]>('/api/app/templates'),
  })

  const { data: rulesData } = useQuery<Rule[]>({
    queryKey: ['app-rules-for-bar'],
    queryFn: async () => apiFetch<Rule[]>('/api/app/rules'),
  })

  // Exclude every personal-* slug: the caller's own toggles via "Persoonlijk"
  // and other users' personal KBs should never show up in the chat dropdown.
  const allKbs = (kbsData?.knowledge_bases ?? []).filter((kb) => !kb.slug.startsWith('personal-'))
  const allSlugs = allKbs.map((kb) => kb.slug)
  const currentSlugs: string[] = pref
    ? pref.kb_slugs_filter === null ? allSlugs : pref.kb_slugs_filter.filter((s) => allSlugs.includes(s))
    : allSlugs

  const mutation = useMutation({
    mutationFn: async (patch: Partial<Omit<KBPref, 'kb_pref_version'>>) =>
      apiFetch<KBPref>('/api/app/account/kb-preference', { method: 'PATCH', body: JSON.stringify(patch) }),
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: ['kb-preference'] })
      const prev = queryClient.getQueryData<KBPref>(['kb-preference'])
      if (prev) queryClient.setQueryData<KBPref>(['kb-preference'], { ...prev, ...patch })
      return { prev }
    },
    onSuccess: (data) => queryClient.setQueryData(['kb-preference'], data),
    onError: (_e, _p, ctx) => { if (ctx?.prev) queryClient.setQueryData(['kb-preference'], ctx.prev) },
  })

  function toggleSlug(slug: string) {
    const next = currentSlugs.includes(slug) ? currentSlugs.filter((s) => s !== slug) : [...currentSlugs, slug]
    // null = all on, [] = none, anything else = explicit subset.
    // DO NOT collapse empty to null — that would flip "turn last off" into
    // "turn everything back on" and break the user's intent.
    mutation.mutate({ kb_slugs_filter: next.length === allSlugs.length ? null : next })
  }

  const allActive = (pref?.kb_personal_enabled ?? false) && currentSlugs.length === allSlugs.length
  function toggleAll() {
    if (allActive) {
      // Turn everything off: empty filter list + disable personal
      mutation.mutate({ kb_slugs_filter: [], kb_personal_enabled: false })
    } else {
      // Turn everything on: null filter (= all org KBs) + enable personal
      mutation.mutate({ kb_slugs_filter: null, kb_personal_enabled: true })
    }
  }

  if (!pref || allKbs.length === 0) return null

  // Build list of active collection names
  const activeNames: string[] = []
  if (pref.kb_personal_enabled) activeNames.push('Persoonlijk')
  for (const kb of allKbs) {
    if (currentSlugs.includes(kb.slug)) activeNames.push(kb.name)
  }

  // Templates — multi-select via pref.active_template_ids
  const allTemplates = templatesData ?? []
  const activeTemplateIds: number[] = pref.active_template_ids ?? []
  const activeTemplates = allTemplates.filter((t) => activeTemplateIds.includes(t.id))

  function toggleTemplate(id: number) {
    const next = activeTemplateIds.includes(id)
      ? activeTemplateIds.filter((x) => x !== id)
      : [...activeTemplateIds, id]
    // [] and null both mean "no templates". Use [] to stay explicit per toggle.
    mutation.mutate({ active_template_ids: next.length === 0 ? null : next })
  }

  function clearTemplates() {
    mutation.mutate({ active_template_ids: null })
  }

  // Rules — status only, caller navigates to /app/rules to manage
  const allRules = rulesData ?? []
  const activeRulesCount = allRules.filter((r) => r.is_active).length

  return (
    <div className="flex shrink-0 items-center gap-4 bg-[var(--color-sidebar)] border-b border-[var(--color-sidebar-border)] pl-4 pr-4 pt-5 pb-4">
      {collOpen && <div className="fixed inset-0 z-40" onClick={() => setCollOpen(false)} />}
      {tmplOpen && <div className="fixed inset-0 z-40" onClick={() => setTmplOpen(false)} />}

      {/* Chat met: (knowledge collections) */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-[13px] text-gray-400 whitespace-nowrap">Chat met:</span>

        <div className="relative z-50 min-w-0">
          <button type="button" onClick={() => setCollOpen((v) => !v)}
            className="flex items-center gap-1.5 text-[14px] font-semibold text-gray-700 hover:text-gray-900 transition-colors truncate">
            <span className="truncate">{activeNames.length > 0 ? activeNames.join(', ') : 'Geen kennis geselecteerd'}</span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-40" />
          </button>

          {collOpen && (
            <div className="absolute left-0 top-full z-50 mt-2 w-64 rounded-lg border border-gray-200 bg-white py-1.5 shadow-lg">
              <div className="flex items-center justify-between px-3 py-1.5">
                <span className="text-[10px] font-semibold tracking-wide text-gray-400">Collecties</span>
                <button
                  type="button"
                  onClick={toggleAll}
                  className="text-[10px] font-semibold tracking-wide text-gray-500 hover:text-gray-900 transition-colors"
                >
                  {allActive ? 'Alles uit' : 'Alles aan'}
                </button>
              </div>
              {/* Personal */}
              <button type="button" onClick={() => mutation.mutate({ kb_personal_enabled: !pref.kb_personal_enabled })}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] hover:bg-gray-50 transition-colors text-left">
                <span className={`h-2 w-2 shrink-0 rounded-full ${pref.kb_personal_enabled ? 'bg-green-500' : 'bg-gray-200'}`} />
                <span className={pref.kb_personal_enabled ? 'text-gray-900 font-medium' : 'text-gray-400'}>Persoonlijk</span>
              </button>
              {/* Org KBs */}
              {allKbs.map((kb) => (
                <button key={kb.slug} type="button" onClick={() => toggleSlug(kb.slug)}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] hover:bg-gray-50 transition-colors text-left">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${currentSlugs.includes(kb.slug) ? 'bg-green-500' : 'bg-gray-200'}`} />
                  <span className={currentSlugs.includes(kb.slug) ? 'text-gray-900 font-medium' : 'text-gray-400'}>{kb.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Templates: multi-select. Hidden when no templates exist for the org/user. */}
      {allTemplates.length > 0 && (
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[13px] text-gray-400 whitespace-nowrap">{m.chatbar_templates_label()}:</span>

          <div className="relative z-50 min-w-0">
            <button type="button" onClick={() => setTmplOpen((v) => !v)}
              className="flex items-center gap-1.5 text-[14px] font-semibold text-gray-700 hover:text-gray-900 transition-colors truncate">
              <span className="truncate">
                {activeTemplates.length > 0
                  ? activeTemplates.map((t) => t.name).join(', ')
                  : m.chatbar_templates_empty()}
              </span>
              <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-40" />
            </button>

            {tmplOpen && (
              <div className="absolute left-0 top-full z-50 mt-2 w-64 rounded-lg border border-gray-200 bg-white py-1.5 shadow-lg">
                <div className="flex items-center justify-between px-3 py-1.5">
                  <span className="text-[10px] font-semibold tracking-wide text-gray-400">
                    {m.chatbar_templates_label()}
                  </span>
                  <button
                    type="button"
                    onClick={clearTemplates}
                    className="text-[10px] font-semibold tracking-wide text-gray-500 hover:text-gray-900 transition-colors"
                  >
                    {m.chatbar_templates_clear()}
                  </button>
                </div>
                {allTemplates.map((t) => {
                  const active = activeTemplateIds.includes(t.id)
                  return (
                    <button key={t.id} type="button" onClick={() => toggleTemplate(t.id)}
                      className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] hover:bg-gray-50 transition-colors text-left">
                      <span className={`h-2 w-2 shrink-0 rounded-full ${active ? 'bg-green-500' : 'bg-gray-200'}`} />
                      <span className={active ? 'text-gray-900 font-medium' : 'text-gray-400'}>{t.name}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Rules status chip — read-only, click to manage at /app/rules */}
      <button
        type="button"
        onClick={() => void navigate({ to: '/app/rules' })}
        className="flex items-center gap-1.5 text-[13px] text-gray-400 hover:text-gray-700 transition-colors whitespace-nowrap"
      >
        <Shield className="h-3.5 w-3.5 shrink-0 opacity-60" />
        <span className={activeRulesCount > 0 ? 'text-gray-700 font-medium' : undefined}>
          {m.chatbar_rules_active({ count: activeRulesCount })}
        </span>
      </button>
    </div>
  )
}
