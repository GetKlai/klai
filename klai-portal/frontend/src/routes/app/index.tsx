import { createFileRoute } from '@tanstack/react-router'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'

import { apiFetch } from '@/lib/apiFetch'
import { chatKbLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

import { KBScopeBar } from './_components/KBScopeBar'

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
    return `https://chat-${tenant}.${rest.join('.')}`
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
      // Health endpoint itself failed — still try loading the iframe as fallback.
      setIframeSrc(getIframeSrc(baseUrl))
      setPhase('loading_iframe')
    }
  }, [token, baseUrl, clearStuckTimer])

  // Run health check on mount and when token becomes available
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

  // Listen for SSO failure from the login page running inside the iframe.
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
    void auth.signinRedirect({ state: { returnTo: '/app/' } })
  }, [auth])

  const showOverlay = phase === 'health_check' || phase === 'loading_iframe'
  const showError = phase === 'error' || phase === 'stuck'

  return (
    <div className="flex h-full w-full flex-col" data-help-id="chat-page">
      <KBScopeBar />
      <div className="relative flex-1">
        {/* Loading overlay */}
        {showOverlay && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-[var(--color-background)]">
            <Loader2 className="h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" />
            <span className="text-sm text-[var(--color-muted-foreground)]">
              {m.chat_health_loading()}
            </span>
          </div>
        )}

        {/* Error / stuck state */}
        {showError && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-4 bg-[var(--color-background)]">
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
    </div>
  )
}
