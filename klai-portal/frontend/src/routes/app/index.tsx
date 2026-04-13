import { createFileRoute } from '@tanstack/react-router'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'

import { apiFetch } from '@/lib/apiFetch'
import { chatKbLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, Plus, Check } from 'lucide-react'
import { Link } from '@tanstack/react-router'

// Threshold: 25 days (conservative — LibreChat refresh tokens are 30d)
const LC_AUTH_KEY = 'lc_authed_at'
const LC_AUTH_TTL_MS = 25 * 24 * 60 * 60 * 1000

// If the iframe hasn't signaled a usable state within this window, assume stuck.
const STUCK_TIMEOUT_MS = 20_000

type Phase = 'health_check' | 'loading_iframe' | 'ready' | 'stuck' | 'error'

interface ChatHealth {
  healthy: boolean
  reason: string | null
}

function useChatBaseUrl(): string {
  return useMemo(() => {
    const { hostname } = window.location
    if (hostname === 'localhost') return 'http://localhost:3080'
    const [tenant, ...rest] = hostname.split('.')
    // dev.getklai.com uses the getklai tenant's chat instance
    const chatTenant = tenant === 'dev' ? 'getklai' : tenant
    return `https://chat-${chatTenant}.${rest.join('.')}`
  }, [])
}

function getIframeSrc(baseUrl: string): string {
  const stored = localStorage.getItem(LC_AUTH_KEY)
  const isFresh = stored !== null && Date.now() - parseInt(stored, 10) < LC_AUTH_TTL_MS
  return isFresh ? baseUrl : `${baseUrl}/oauth/openid`
}

function getErrorMessage(reason: string | null): string {
  switch (reason) {
    case 'not_provisioned':
      return m.chat_health_failed_not_provisioned()
    case 'provisioning_in_progress':
      return m.chat_health_failed_provisioning()
    case 'sso_expired':
      return m.chat_health_failed_sso_expired()
    default:
      return m.chat_health_failed_generic()
  }
}

export const Route = createFileRoute('/app/')({
  component: ChatHome,
})

function ChatHome() {
  const baseUrl = useChatBaseUrl()
  const auth = useAuth()
  const token = auth.user?.access_token

  const [phase, setPhase] = useState<Phase>('health_check')
  const [errorReason, setErrorReason] = useState<string | null>(null)
  const [iframeSrc, setIframeSrc] = useState<string | null>(null)
  const stuckTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearStuckTimer = useCallback(() => {
    if (stuckTimer.current) {
      clearTimeout(stuckTimer.current)
      stuckTimer.current = null
    }
  }, [])

  const runHealthCheck = useCallback(async () => {
    if (!token) return

    setPhase('health_check')
    setErrorReason(null)

    try {
      const health = await apiFetch<ChatHealth>('/api/app/chat-health', token)

      if (!health.healthy) {
        chatKbLogger.warn('Chat health check failed', { reason: health.reason })
        setPhase('error')
        setErrorReason(health.reason)
        return
      }

      // Health OK — load the iframe
      setIframeSrc(getIframeSrc(baseUrl))
      setPhase('loading_iframe')

      // Start stuck detection timer
      clearStuckTimer()
      stuckTimer.current = setTimeout(() => {
        setPhase((current) => {
          if (current === 'loading_iframe') {
            chatKbLogger.warn('Chat iframe stuck — did not reach ready state within timeout')
            return 'stuck'
          }
          return current
        })
      }, STUCK_TIMEOUT_MS)
    } catch (err) {
      chatKbLogger.error('Chat health check request failed', { err })
      setIframeSrc(getIframeSrc(baseUrl))
      setPhase('loading_iframe')
    }
  }, [token, baseUrl, clearStuckTimer])

  useEffect(() => {
    if (token) {
      void runHealthCheck()
    }
    return clearStuckTimer
  }, [token, runHealthCheck, clearStuckTimer])

  const handleIframeLoad = useCallback(() => {
    localStorage.setItem(LC_AUTH_KEY, Date.now().toString())
    clearStuckTimer()
    setPhase('ready')
  }, [clearStuckTimer])

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return
      if (event.data?.type === 'klai-sso-failed') {
        chatKbLogger.warn('SSO cookie expired — iframe auth failed, triggering re-auth')
        clearStuckTimer()
        setPhase('error')
        setErrorReason('sso_expired')
      }
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [clearStuckTimer])

  const handleRetry = useCallback(() => {
    chatKbLogger.info('Retry: forcing portal re-authentication')
    localStorage.removeItem(LC_AUTH_KEY)
    void auth.signinRedirect({ state: { returnTo: '/app' } })
  }, [auth])

  const showOverlay = phase === 'health_check' || phase === 'loading_iframe'
  const showError = phase === 'error' || phase === 'stuck'

  return (
    <div className="flex h-full w-full" data-help-id="chat-page">
      {/* Chat area */}
      <div className="relative flex-1 flex flex-col">
        {/* Loading overlay */}
        {showOverlay && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-[var(--color-secondary)]">
            <Loader2 className="h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" />
            <span className="text-sm text-[var(--color-muted-foreground)]">
              {m.chat_health_loading()}
            </span>
          </div>
        )}

        {/* Error / stuck state */}
        {showError && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-4 bg-[var(--color-secondary)]">
            <AlertTriangle className="h-8 w-8 text-[var(--color-muted-foreground)]" />
            <div className="text-center">
              <p className="text-sm font-medium text-[var(--color-foreground)]">
                {phase === 'stuck' ? m.chat_health_stuck_title() : m.chat_health_failed_title()}
              </p>
              <p className="mt-1 max-w-sm text-xs text-[var(--color-muted-foreground)]">
                {phase === 'stuck'
                  ? m.chat_health_stuck_description()
                  : getErrorMessage(errorReason)}
              </p>
            </div>
            <button
              type="button"
              onClick={handleRetry}
              className="flex items-center gap-2 rounded-full bg-[var(--color-primary)] px-4 py-2 text-sm text-[var(--color-primary-foreground)] transition-colors hover:bg-[var(--color-accent)]"
            >
              <RefreshCw className="h-4 w-4" />
              {m.chat_health_retry()}
            </button>
          </div>
        )}

        {/* LibreChat iframe */}
        {iframeSrc && (
          <iframe
            src={iframeSrc}
            onLoad={handleIframeLoad}
            className={`h-full w-full border-none transition-opacity duration-200 ${
              phase === 'ready' ? 'opacity-100' : 'opacity-0'
            }`}
            title="Chat"
            allow="clipboard-write; microphone; screen-wake-lock"
          />
        )}
      </div>

      {/* Knowledge panel — always visible */}
      <KnowledgePanel token={token} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Knowledge side panel
// ---------------------------------------------------------------------------

interface KBPref {
  kb_retrieval_enabled: boolean
  kb_personal_enabled: boolean
  kb_slugs_filter: string[] | null
  kb_narrow: boolean
  kb_pref_version: number
}

interface KBItem {
  id: number
  name: string
  slug: string
  owner_type: string
  owner_user_id: string | null
}

interface KBStats {
  items: number
  connectors: number
}

function KnowledgePanel({ token }: { token: string | undefined }) {
  const auth = useAuth()
  const myUserId = auth.user?.profile?.sub
  const queryClient = useQueryClient()

  const { data: pref } = useQuery<KBPref>({
    queryKey: ['kb-preference'],
    queryFn: async () => apiFetch<KBPref>('/api/app/account/kb-preference', token),
    enabled: !!token,
  })

  const { data: kbsData } = useQuery<{ knowledge_bases: KBItem[] }>({
    queryKey: ['app-knowledge-bases'],
    queryFn: async () => apiFetch<{ knowledge_bases: KBItem[] }>('/api/app/knowledge-bases', token),
    enabled: !!token,
  })

  const { data: statsData } = useQuery<{ stats: Record<string, KBStats> }>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: async () => apiFetch<{ stats: Record<string, KBStats> }>('/api/app/knowledge-bases/stats-summary', token),
    enabled: !!token,
  })

  const mutation = useMutation({
    mutationFn: async (patch: Partial<Omit<KBPref, 'kb_pref_version'>>) => {
      return apiFetch<KBPref>('/api/app/account/kb-preference', token, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      })
    },
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: ['kb-preference'] })
      const previous = queryClient.getQueryData<KBPref>(['kb-preference'])
      if (previous) {
        queryClient.setQueryData<KBPref>(['kb-preference'], { ...previous, ...patch })
      }
      return { previous }
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['kb-preference'], data)
    },
    onError: (_err, _patch, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['kb-preference'], context.previous)
      }
    },
  })

  const allKbs = kbsData?.knowledge_bases ?? []
  const stats = statsData?.stats ?? {}

  const personalKb = allKbs.find(
    (kb) => kb.slug === `personal-${myUserId}` && kb.owner_type === 'user',
  )
  const otherKbs = allKbs.filter((kb) => kb.slug !== personalKb?.slug)

  const allSlugs = otherKbs.map((kb) => kb.slug)
  const currentSlugs: string[] = pref
    ? pref.kb_slugs_filter === null
      ? allSlugs
      : pref.kb_slugs_filter.filter((s) => allSlugs.includes(s))
    : allSlugs

  function toggleSlug(slug: string) {
    const next = currentSlugs.includes(slug)
      ? currentSlugs.filter((s) => s !== slug)
      : [...currentSlugs, slug]
    const normalized: string[] | null =
      next.length === 0 || next.length === allSlugs.length ? null : next
    mutation.mutate({ kb_slugs_filter: normalized })
  }

  function togglePersonal() {
    mutation.mutate({ kb_personal_enabled: !pref!.kb_personal_enabled })
  }

  // Total items across active collections
  const totalItems = Object.values(stats).reduce((sum, s) => sum + s.items, 0)

  if (!pref || allKbs.length === 0) return null

  return (
    <aside className="flex w-64 shrink-0 flex-col border-l border-[var(--color-border)] bg-[var(--color-card)]">
      {/* Header */}
      <div className="px-5 pt-5 pb-4">
        <h2 className="text-sm font-medium text-[var(--color-foreground)]">Je kennis</h2>
        <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
          {totalItems} items in {allKbs.length} collecties
        </p>
      </div>

      {/* Collections */}
      <div className="flex-1 overflow-y-auto px-3 space-y-1">
        {personalKb && (
          <CollectionCard
            name={m.chat_kb_bar_personal_label()}
            items={stats[personalKb.slug]?.items ?? 0}
            connectors={stats[personalKb.slug]?.connectors ?? 0}
            active={pref.kb_personal_enabled}
            onClick={togglePersonal}
            pending={mutation.isPending}
          />
        )}

        {otherKbs.map((kb) => (
          <CollectionCard
            key={kb.slug}
            name={kb.name}
            items={stats[kb.slug]?.items ?? 0}
            connectors={stats[kb.slug]?.connectors ?? 0}
            active={currentSlugs.includes(kb.slug)}
            onClick={() => toggleSlug(kb.slug)}
            pending={mutation.isPending}
          />
        ))}
      </div>

      {/* Footer */}
      <div className="px-5 py-4">
        <Link
          to="/app/knowledge"
          className="flex items-center justify-center gap-1.5 rounded-full border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-muted-foreground)] transition-colors hover:border-[var(--color-foreground)]/30 hover:text-[var(--color-foreground)]"
        >
          <Plus size={14} strokeWidth={1.5} />
          Beheer bronnen
        </Link>
      </div>
    </aside>
  )
}

function CollectionCard({
  name,
  items,
  connectors,
  active,
  onClick,
  pending,
}: {
  name: string
  items: number
  connectors: number
  active: boolean
  onClick: () => void
  pending: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      className={[
        'flex w-full items-start gap-3 rounded-xl px-3 py-3 text-left transition-all',
        pending ? 'opacity-50' : '',
        active
          ? 'bg-[var(--color-background)] shadow-sm border border-[var(--color-border)]'
          : 'hover:bg-[var(--color-background)]/60 border border-transparent',
      ].join(' ')}
    >
      <span className={[
        'mt-0.5 h-2 w-2 shrink-0 rounded-full transition-colors',
        active ? 'bg-[var(--color-success)]' : 'bg-[var(--color-border)]',
      ].join(' ')} />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-[var(--color-foreground)]">{name}</p>
        <p className="mt-0.5 text-[10px] text-[var(--color-muted-foreground)]">
          {items > 0 ? `${items} items` : 'Leeg'}
          {connectors > 0 && ` · ${connectors} bronnen`}
        </p>
      </div>
    </button>
  )
}
