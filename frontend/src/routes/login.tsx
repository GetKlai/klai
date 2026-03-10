import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, Lock, Shield } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { setLocale } from '@/paraglide/runtime'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

type Locale = 'nl' | 'en'

function getInitialLocale(): Locale {
  const saved = localStorage.getItem('klai-locale')
  return saved === 'en' ? 'en' : 'nl'
}

type SearchParams = {
  authRequest?: string
}

export const Route = createFileRoute('/login')({
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    authRequest: typeof search.authRequest === 'string' ? search.authRequest : undefined,
  }),
  component: LoginPage,
})

function LoginPage() {
  const { authRequest: authRequestId } = Route.useSearch()
  const navigate = useNavigate()

  const [locale, setLocaleState] = useState<Locale>(() => {
    const initial = getInitialLocale()
    setLocale(initial)
    return initial
  })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // True while checking for an existing portal SSO session
  const [checkingSSO, setCheckingSSO] = useState(!!authRequestId)

  // TOTP challenge state — shown after a successful password check
  const [totpStep, setTotpStep] = useState(false)
  const [tempToken, setTempToken] = useState<string | null>(null)
  const [totpCode, setTotpCode] = useState('')

  useEffect(() => {
    if (!authRequestId) return

    // Try to silently complete the auth request using the portal SSO session.
    // The klai_sso cookie (set during portal login) is sent automatically by the browser.
    async function trySSO() {
      try {
        const resp = await fetch(`${API_BASE}/api/auth/sso-complete`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ auth_request_id: authRequestId }),
        })
        if (resp.ok) {
          const { callback_url } = await resp.json()
          window.location.href = callback_url
          return
        }
      } catch {
        // Network error — fall through to login form
      }
      setCheckingSSO(false)
    }

    trySSO()
  }, [authRequestId])

  function switchLocale(l: Locale) {
    setLocale(l)
    setLocaleState(l)
    localStorage.setItem('klai-locale', l)
  }

  // If Zitadel didn't supply an authRequestId, the user arrived here directly.
  // Send them back to / so signinRedirect() can start the OIDC flow properly.
  if (!authRequestId) {
    navigate({ to: '/' })
    return null
  }

  // Still checking for existing SSO session — show a spinner
  if (checkingSSO) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
      </div>
    )
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const resp = await fetch(`${API_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, auth_request_id: authRequestId }),
      })

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        setError(data?.detail ?? m.login_submit())
        return
      }

      const data = await resp.json()

      if (data.status === 'totp_required') {
        setTempToken(data.temp_token)
        setTotpStep(true)
        return
      }

      // Navigate to the OIDC callback URL — react-oidc-context picks it up from there
      window.location.href = data.callback_url
    } catch {
      setError(m.login_error_connection())
    } finally {
      setLoading(false)
    }
  }

  async function handleTotpSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const resp = await fetch(`${API_BASE}/api/auth/totp-login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          temp_token: tempToken,
          code: totpCode,
          auth_request_id: authRequestId,
        }),
      })

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        setError(data?.detail ?? m.totp_submit())
        return
      }

      const { callback_url } = await resp.json()
      window.location.href = callback_url
    } catch {
      setError(m.totp_error_connection())
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      {/* Left panel — branding */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between bg-[var(--color-purple-deep)] p-12 text-[var(--color-sand-light)]">
        <div>
          <img src="/klai-logo-white.svg" alt="Klai" className="h-8 w-auto" />
        </div>

        <div className="space-y-6">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            {m.login_hero_heading()}
            <br />
            <span className="text-[var(--color-purple-accent)]">{m.login_hero_highlight()}</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            {m.login_hero_body()}
          </p>

          <div className="flex flex-col gap-3 pt-4">
            <div className="flex items-center gap-3 text-sm text-[var(--color-sand-mid)]">
              <Shield size={16} className="shrink-0 text-[var(--color-purple-accent)]" />
              {m.login_hero_bullet_eu()}
            </div>
            <div className="flex items-center gap-3 text-sm text-[var(--color-sand-mid)]">
              <Lock size={16} className="shrink-0 text-[var(--color-purple-accent)]" />
              {m.login_hero_bullet_open()}
            </div>
          </div>
        </div>


      </div>

      {/* Right panel */}
      <div className="flex w-full flex-col items-center justify-center px-8 lg:w-1/2">
        <div className="w-full max-w-sm space-y-8">
          <div className="flex items-center justify-between">
            <div className="lg:hidden">
              <img src="/klai-logo.svg" alt="Klai" className="h-8 w-auto" />
            </div>
            <div className="ml-auto flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
              <button
                onClick={() => switchLocale('nl')}
                className={locale === 'nl' ? 'font-semibold text-[var(--color-purple-deep)]' : 'opacity-40 hover:opacity-70'}
              >
                NL
              </button>
              <span className="opacity-30">/</span>
              <button
                onClick={() => switchLocale('en')}
                className={locale === 'en' ? 'font-semibold text-[var(--color-purple-deep)]' : 'opacity-40 hover:opacity-70'}
              >
                EN
              </button>
            </div>
          </div>

          {totpStep ? (
            /* TOTP challenge step */
            <>
              <div className="space-y-2">
                <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
                  {m.totp_heading()}
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {m.totp_subheading()}
                </p>
              </div>

              <form onSubmit={handleTotpSubmit} className="space-y-4">
                <div className="space-y-1">
                  <label htmlFor="totp-code" className="block text-sm font-medium text-[var(--color-foreground)]">
                    {m.totp_field_code()}
                  </label>
                  <input
                    id="totp-code"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    maxLength={6}
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ''))}
                    required
                    autoComplete="one-time-code"
                    autoFocus
                    className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20 tracking-widest text-center text-lg font-mono"
                  />
                </div>

                {error && (
                  <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
                )}

                <Button type="submit" size="lg" className="w-full gap-3" disabled={loading || totpCode.length !== 6}>
                  {loading ? m.totp_submit_loading() : m.totp_submit()}
                  {!loading && <ArrowRight size={16} />}
                </Button>
              </form>

              <p className="text-center text-xs text-[var(--color-muted-foreground)]">
                <button
                  onClick={() => { setTotpStep(false); setTotpCode(''); setError(null) }}
                  className="text-[var(--color-purple-muted)] hover:underline"
                >
                  Terug
                </button>
              </p>
            </>
          ) : (
            /* Password login step */
            <>
              <div className="space-y-2">
                <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
                  {m.login_heading()}
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {m.login_subheading()}
                </p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-1">
                  <label htmlFor="email" className="block text-sm font-medium text-[var(--color-foreground)]">
                    {m.login_field_email()}
                  </label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoComplete="email"
                    autoFocus
                    className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
                  />
                </div>

                <div className="space-y-1">
                  <label htmlFor="password" className="block text-sm font-medium text-[var(--color-foreground)]">
                    {m.login_field_password()}
                  </label>
                  <input
                    id="password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="current-password"
                    className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
                  />
                </div>

                {error && (
                  <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
                )}

                <Button type="submit" size="lg" className="w-full gap-3" disabled={loading}>
                  {loading ? m.login_submit_loading() : m.login_submit()}
                  {!loading && <ArrowRight size={16} />}
                </Button>
              </form>

              <div className="flex items-center justify-between text-center text-xs text-[var(--color-muted-foreground)]">
                <a
                  href={`/password/forgot${email ? `?email=${encodeURIComponent(email)}` : ''}`}
                  className="text-[var(--color-purple-muted)] hover:underline"
                >
                  {m.login_forgot_password()}
                </a>
                <a href="/signup" className="text-[var(--color-purple-muted)] hover:underline">
                  {m.login_no_account()}
                </a>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
