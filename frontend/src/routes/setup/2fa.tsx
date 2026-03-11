import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import QRCode from 'react-qr-code'
import { Button } from '@/components/ui/button'
import { ArrowRight, Shield } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export const Route = createFileRoute('/setup/2fa')({
  component: Setup2FAPage,
})

function Setup2FAPage() {
  useLocale()
  const auth = useAuth()

  const [uri, setUri] = useState<string | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!auth.isAuthenticated || !auth.user) return

    fetch(`${API_BASE}/api/auth/totp/setup`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.user.access_token}` },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data) => {
        setUri(data.uri)
        setSecret(data.secret)
      })
      .catch(() => {
        setLoadError(m.error_connection())
      })
  }, [auth.isAuthenticated, auth.user])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const resp = await fetch(`${API_BASE}/api/auth/totp/confirm`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${auth.user!.access_token}`,
        },
        body: JSON.stringify({ code }),
      })

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        setError(data?.detail ?? m.setup_2fa_error_invalid_code())
        return
      }

      setDone(true)
      // Clear the TOTP secret from state now that setup is confirmed
      setSecret(null)
      setUri(null)
      // Short delay so user sees the success state before redirect
      setTimeout(() => {
        const isAdmin = sessionStorage.getItem('klai:isAdmin') === 'true'
        window.location.replace(isAdmin ? '/admin' : '/app')
      }, 1500)
    } catch {
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
  }

  if (!auth.isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
      </div>
    )
  }

  const leftContent = (
    <>
      <h1 className="font-serif text-4xl font-bold leading-tight">
        {m.setup_2fa_hero_heading()}
        <br />
        <span className="text-[var(--color-purple-accent)]">{m.setup_2fa_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
        {m.setup_2fa_hero_body()}
      </p>
      <div className="flex items-center gap-3 text-sm text-[var(--color-sand-mid)]">
        <Shield size={16} className="shrink-0 text-[var(--color-purple-accent)]" />
        Google Authenticator, Authy, 1Password
      </div>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent}>
      {done ? (
        <div className="space-y-4 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-purple-deep)]">
            <Shield size={22} className="text-[var(--color-sand-light)]" />
          </div>
          <p className="font-serif text-xl font-bold text-[var(--color-purple-deep)]">
            {m.setup_2fa_done_heading()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.setup_2fa_done_body()}
          </p>
        </div>
      ) : loadError ? (
        <div className="space-y-3 text-center">
          <p className="text-sm text-red-700">{loadError}</p>
          <button
            onClick={() => window.location.reload()}
            className="text-xs text-[var(--color-purple-muted)] hover:underline"
          >
            {m.setup_2fa_retry()}
          </button>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {m.setup_2fa_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.setup_2fa_subheading()}
            </p>
          </div>

          {/* QR code */}
          <div className="flex flex-col items-center gap-4">
            {uri ? (
              <div className="rounded-xl border border-[var(--color-border)] bg-white p-4">
                <QRCode value={uri} size={180} />
              </div>
            ) : (
              <div className="flex h-[212px] w-[212px] items-center justify-center rounded-xl border border-[var(--color-border)] bg-white">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
              </div>
            )}

            {secret && (
              <details className="w-full text-center">
                <summary className="cursor-pointer text-xs text-[var(--color-purple-muted)] hover:underline select-none">
                  {m.setup_2fa_manual_label()}
                </summary>
                <p className="mt-2 rounded-lg bg-[var(--color-border)] px-3 py-2 font-mono text-xs tracking-widest break-all">
                  {secret}
                </p>
              </details>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="totp-code" className="block text-sm font-medium text-[var(--color-foreground)]">
                {m.setup_2fa_field_code()}
              </label>
              <input
                id="totp-code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                required
                autoComplete="one-time-code"
                autoFocus
                className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20 tracking-widest text-center text-lg font-mono"
              />
            </div>

            {error && (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
            )}

            <Button
              type="submit"
              size="lg"
              className="w-full gap-3"
              disabled={loading || code.length !== 6 || !uri}
            >
              {loading ? m.setup_2fa_submit_loading() : m.setup_2fa_submit()}
              {!loading && <ArrowRight size={16} />}
            </Button>
          </form>
        </>
      )}
    </AuthPageLayout>
  )
}
