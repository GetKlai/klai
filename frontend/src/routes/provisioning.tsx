import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { CheckCircle, AlertCircle } from 'lucide-react'
import * as m from '@/paraglide/messages'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''
const POLL_INTERVAL_MS = 3000
const TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes

export const Route = createFileRoute('/provisioning')((({
  component: ProvisioningPage,
})))

type Status = 'polling' | 'ready' | 'failed' | 'timeout'

function ProvisioningPage() {
  const auth = useAuth()
  const [status, setStatus] = useState<Status>('polling')
  const [dots, setDots] = useState('')
  const startedAt = useRef(Date.now())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Animated dots for the loading state
  useEffect(() => {
    const id = setInterval(() => {
      setDots((d) => (d.length >= 3 ? '' : d + '.'))
    }, 500)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (auth.isLoading || !auth.isAuthenticated) return

    async function poll() {
      if (Date.now() - startedAt.current > TIMEOUT_MS) {
        setStatus('timeout')
        return
      }

      try {
        const res = await fetch(`${API_BASE}/api/me`, {
          headers: { Authorization: `Bearer ${auth.user!.access_token}` },
        })
        if (!res.ok) {
          timer.current = setTimeout(poll, POLL_INTERVAL_MS)
          return
        }
        const me = await res.json()

        if (me.provisioning_status === 'ready' && me.workspace_url) {
          setStatus('ready')
          const currentHost = window.location.hostname
          const workspaceHost = new URL(me.workspace_url).hostname
          if (currentHost !== workspaceHost) {
            // Redirect to tenant subdomain, preserve OIDC tokens via re-login
            window.location.replace(me.workspace_url)
          } else {
            // Already on the right subdomain — go to admin
            window.location.replace('/admin')
          }
          return
        }

        if (me.provisioning_status === 'failed') {
          setStatus('failed')
          return
        }

        // Still pending — poll again
        timer.current = setTimeout(poll, POLL_INTERVAL_MS)
      } catch {
        timer.current = setTimeout(poll, POLL_INTERVAL_MS)
      }
    }

    poll()
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [auth.isLoading, auth.isAuthenticated, auth.user])

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
      <div className="w-full max-w-sm space-y-6 px-6 text-center">
        <img src="/klai-logo.svg" alt="Klai" className="h-7 w-auto mx-auto" />

        {(status === 'polling') && (
          <>
            <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
            <div className="space-y-2">
              <p className="font-serif text-lg font-semibold text-[var(--color-purple-deep)]">
                {m.provisioning_polling_title()}{dots}
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                {m.provisioning_polling_subtitle()}
              </p>
            </div>
          </>
        )}

        {status === 'ready' && (
          <>
            <CheckCircle
              size={40}
              className="mx-auto text-[var(--color-purple-accent)]"
              strokeWidth={1.5}
            />
            <p className="font-serif text-lg font-semibold text-[var(--color-purple-deep)]">
              {m.provisioning_ready_title()}
            </p>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.provisioning_ready_subtitle()}
            </p>
          </>
        )}

        {(status === 'failed' || status === 'timeout') && (
          <>
            <AlertCircle
              size={40}
              className="mx-auto text-red-400"
              strokeWidth={1.5}
            />
            <div className="space-y-2">
              <p className="font-serif text-lg font-semibold text-[var(--color-purple-deep)]">
                {m.provisioning_failed_title()}
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                {m.provisioning_failed_body()}{' '}
                <a
                  href="mailto:support@getklai.com"
                  className="font-medium text-[var(--color-purple-muted)] underline"
                >
                  support@getklai.com
                </a>
                .
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
