import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, Lock, Shield } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { API_BASE } from '@/lib/api'
import { useLocale } from '@/lib/locale'
import { authLogger } from '@/lib/logger'

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
  const { locale } = useLocale()

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

  const inIframe = window.self !== window.top

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
      } catch (err) {
        authLogger.warn('Silent SSO completion failed', err)
      }

      // SSO failed. If we're inside the LibreChat iframe, showing a login form is
      // useless — the user can't interact with it. Notify the parent frame so it
      // can show an error/retry UI instead of hanging forever on "Welcome back".
      if (inIframe) {
        authLogger.warn('SSO failed inside iframe — notifying parent frame')
        window.parent.postMessage({ type: 'klai-sso-failed' }, window.location.origin)
        return
      }

      setCheckingSSO(false)
    }

    void trySSO()
  }, [authRequestId, inIframe])

  // If Zitadel didn't supply an authRequestId, the user arrived here directly.
  // Send them back to / so signinRedirect() can start the OIDC flow properly.
  if (!authRequestId) {
    void navigate({ to: '/' })
    return null
  }

  // Still checking for existing SSO session — show a spinner
  if (checkingSSO) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  async function handleSocialLogin(idpId: string) {
    setError(null)
    setLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/api/auth/idp-intent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idp_id: idpId, auth_request_id: authRequestId }),
      })
      if (!resp.ok) {
        setError(m.login_error_generic())
        return
      }
      const { auth_url } = await resp.json()
      window.location.href = auth_url
    } catch (err) {
      authLogger.warn('Social login request failed', err)
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
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
        // Stale auth request — restart the OIDC flow from scratch
        if (resp.status === 409 && data?.detail === 'auth_request_stale') {
          authLogger.warn('Auth request stale, restarting OIDC flow')
          window.location.href = '/'
          return
        }
        setError(data?.detail ?? m.login_error_generic())
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
    } catch (err) {
      authLogger.warn('Password login request failed', err)
      setError(m.error_connection())
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
        // Stale auth request — restart the OIDC flow from scratch
        if (resp.status === 409 && data?.detail === 'auth_request_stale') {
          authLogger.warn('Auth request stale, restarting OIDC flow')
          window.location.href = '/'
          return
        }
        setError(data?.detail ?? m.totp_error_generic())
        return
      }

      const { callback_url } = await resp.json()
      window.location.href = callback_url
    } catch (err) {
      authLogger.warn('TOTP verification request failed', err)
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
  }

  const leftContent = (
    <>
      <h1 className="text-3xl lg:text-4xl font-semibold leading-[1.15] tracking-tight">
        {m.login_hero_heading()}
        <br />
        <span className="font-accent not-italic text-[var(--color-rl-accent)]">
          {m.login_hero_highlight()}
        </span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]/90 max-w-md">
        {m.login_hero_body()}
      </p>
      <div className="flex flex-col gap-3.5 pt-2">
        <div className="flex items-center gap-3 text-sm text-[var(--color-rl-cream)]/90">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--color-rl-accent)]/15 ring-1 ring-[var(--color-rl-accent)]/30">
            <Shield size={14} className="text-[var(--color-rl-accent)]" />
          </span>
          {m.login_hero_bullet_eu()}
        </div>
        <div className="flex items-center gap-3 text-sm text-[var(--color-rl-cream)]/90">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--color-rl-accent)]/15 ring-1 ring-[var(--color-rl-accent)]/30">
            <Lock size={14} className="text-[var(--color-rl-accent)]" />
          </span>
          {m.login_hero_bullet_open()}
        </div>
      </div>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      {totpStep ? (
        /* TOTP challenge step */
        <>
          <div className="space-y-1.5">
            <h2 className="text-2xl font-semibold tracking-tight text-[var(--color-foreground)]">
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
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)] tracking-widest text-center text-base font-mono"
              />
            </div>

            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}

            <Button type="submit" size="lg" className="w-full gap-3" disabled={loading || totpCode.length !== 6}>
              {loading ? m.totp_submit_loading() : m.totp_submit()}
              {!loading && <ArrowRight size={16} />}
            </Button>
          </form>

          <p className="text-center text-xs text-[var(--color-muted-foreground)]">
            <button
              onClick={() => { setTotpStep(false); setTotpCode(''); setError(null) }}
              className="text-[var(--color-rl-accent-dark)] hover:underline"
            >
              {m.totp_back()}
            </button>
          </p>
        </>
      ) : (
        /* Password login step */
        <>
          <div className="space-y-1.5">
            <h2 className="text-2xl font-semibold tracking-tight text-[var(--color-foreground)]">
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
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
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
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>

            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}

            <Button type="submit" size="lg" className="w-full gap-3" disabled={loading}>
              {loading ? m.login_submit_loading() : m.login_submit()}
              {!loading && <ArrowRight size={16} />}
            </Button>
          </form>

          {/* Social login */}
          <div className="relative flex items-center gap-3 pt-1">
            <div className="h-px flex-1 bg-[var(--color-border)]" />
            <span className="text-xs uppercase tracking-[0.12em] text-[var(--color-muted-foreground)]">
              {m.login_or_continue_with()}
            </span>
            <div className="h-px flex-1 bg-[var(--color-border)]" />
          </div>

          <div className="flex flex-col gap-2">
            <button
              type="button"
              onClick={() => handleSocialLogin('368810756424073247')}
              disabled={loading}
              className="flex w-full items-center justify-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm font-medium text-[var(--color-foreground)] transition hover:bg-[var(--color-muted)] disabled:opacity-50"
            >
              {/* Google G */}
              <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
                <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
                <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
                <path d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332z" fill="#FBBC05"/>
                <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.962L3.964 7.294C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
              </svg>
              {m.login_with_google()}
            </button>

            <button
              type="button"
              onClick={() => handleSocialLogin('368809521386094623')}
              disabled={loading}
              className="flex w-full items-center justify-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm font-medium text-[var(--color-foreground)] transition hover:bg-[var(--color-muted)] disabled:opacity-50"
            >
              {/* Microsoft squares */}
              <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
                <path d="M0 0h8.571v8.571H0z" fill="#F25022"/>
                <path d="M9.429 0H18v8.571H9.429z" fill="#7FBA00"/>
                <path d="M0 9.429h8.571V18H0z" fill="#00A4EF"/>
                <path d="M9.429 9.429H18V18H9.429z" fill="#FFB900"/>
              </svg>
              {m.login_with_microsoft()}
            </button>
          </div>

          <div className="flex items-center justify-between text-center text-xs text-[var(--color-muted-foreground)]">
            <Link
              to="/$locale/password/forgot"
              params={{ locale }}
              search={email ? { email } : {}}
              className="text-[var(--color-rl-accent-dark)] hover:underline"
            >
              {m.login_forgot_password()}
            </Link>
            <Link
              to="/$locale/signup"
              params={{ locale }}
              className="text-[var(--color-rl-accent-dark)] hover:underline"
            >
              {m.login_no_account()}
            </Link>
          </div>
        </>
      )}
    </AuthPageLayout>
  )
}
