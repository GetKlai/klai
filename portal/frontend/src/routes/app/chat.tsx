import { createFileRoute } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import { KBScopeBar } from './_components/KBScopeBar'

// Threshold: 25 days (conservative — LibreChat refresh tokens are 30d)
const LC_AUTH_KEY = 'lc_authed_at'
const LC_AUTH_TTL_MS = 25 * 24 * 60 * 60 * 1000

function useChatBaseUrl(): string {
  return useMemo(() => {
    const { hostname } = window.location
    if (hostname === 'localhost') return 'http://localhost:3080'
    // Portal runs at {tenant}.getklai.com — build chat-{tenant}.getklai.com
    const [tenant, ...rest] = hostname.split('.')
    return `https://chat-${tenant}.${rest.join('.')}`
  }, [])
}

/**
 * If LibreChat was successfully loaded within the last 25 days, skip the OIDC
 * redirect and go straight to the home page. Otherwise trigger /oauth/openid
 * so LibreChat initiates the OIDC flow — which completes silently as long as
 * the Zitadel session is active (configured to 90 days).
 */
function getIframeSrc(baseUrl: string): string {
  const stored = localStorage.getItem(LC_AUTH_KEY)
  const isFresh = stored !== null && Date.now() - parseInt(stored, 10) < LC_AUTH_TTL_MS
  return isFresh ? baseUrl : `${baseUrl}/oauth/openid`
}

export const Route = createFileRoute('/app/chat')({
  component: ChatPage,
})

function ChatPage() {
  const baseUrl = useChatBaseUrl()
  const [src] = useState(() => getIframeSrc(baseUrl))
  const [loaded, setLoaded] = useState(false)

  const handleLoad = useCallback(() => {
    // Mark LibreChat as successfully reached. This fires once the iframe settles
    // on its final page (after any OIDC redirects). If the OIDC flow was needed
    // it has already completed at this point.
    localStorage.setItem(LC_AUTH_KEY, Date.now().toString())
    setLoaded(true)
  }, [])

  return (
    <div className="flex h-full w-full flex-col" data-help-id="chat-page">
      <KBScopeBar />
      <div className="relative flex-1">
        {!loaded && (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--color-secondary)]">
            <Loader2 className="h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" />
          </div>
        )}
        <iframe
          src={src}
          onLoad={handleLoad}
          className={`h-full w-full border-none transition-opacity duration-200 ${loaded ? 'opacity-100' : 'opacity-0'}`}
          title="Chat"
          allow="clipboard-write; microphone; screen-wake-lock"
        />
      </div>
    </div>
  )
}
