import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { useLocale } from '@/lib/locale'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/$locale/signup/social')({
  validateSearch: (search: Record<string, unknown>) => ({
    first_name: typeof search.first_name === 'string' ? search.first_name : '',
    last_name: typeof search.last_name === 'string' ? search.last_name : '',
    email: typeof search.email === 'string' ? search.email : '',
  }),
  component: SocialSignupPage,
})

function SocialSignupPage() {
  const { locale } = useLocale()
  const navigate = useNavigate()
  const { first_name, last_name, email } = Route.useSearch()

  const [companyName, setCompanyName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Guard: if no identity info in URL, redirect back to signup
  if (!email) {
    void navigate({ to: '/$locale/signup', params: { locale } })
    return null
  }

  const displayName = [first_name, last_name].filter(Boolean).join(' ') || email

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!companyName.trim()) return
    setError(null)
    setLoading(true)

    try {
      const resp = await fetch(`${API_BASE}/api/signup/social`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ company_name: companyName.trim() }),
      })

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        if (resp.status === 400 && data?.detail?.toLowerCase().includes('expired')) {
          setError(m.signup_social_expired())
        } else {
          setError(m.signup_social_error_server({ status: String(resp.status) }))
        }
        return
      }

      const data = await resp.json()
      // SSO cookie is set by the backend — navigate to root to trigger OIDC auto-login
      window.location.href = data.redirect_url ?? '/'
    } catch {
      setError(m.signup_error_connection())
    } finally {
      setLoading(false)
    }
  }

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.signup_hero_heading()}
        <br />
        <span className="text-[var(--color-rl-accent)]">{m.signup_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.signup_hero_body()}
      </p>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-1">
        <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.signup_social_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.signup_social_subheading()}
        </p>
      </div>

      {/* Identity confirmation — read-only */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-muted)] px-3 py-2.5">
        <p className="text-xs text-[var(--color-muted-foreground)]">
          {m.signup_social_identity_label()}
        </p>
        <p className="text-sm font-medium text-[var(--color-foreground)]">{displayName}</p>
        {displayName !== email && (
          <p className="text-xs text-[var(--color-muted-foreground)]">{email}</p>
        )}
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1">
          <label
            htmlFor="company_name"
            className="block text-sm font-medium text-[var(--color-foreground)]"
          >
            {m.signup_social_company_label()}
          </label>
          <input
            id="company_name"
            name="company_name"
            type="text"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            required
            autoFocus
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
          />
        </div>

        {error && (
          <div className="space-y-2">
            <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">
              {error}
            </p>
            {error === m.signup_social_expired() && (
              <Link
                to="/$locale/signup"
                params={{ locale }}
                className="block text-center text-sm font-medium text-[var(--color-rl-accent-dark)] underline"
              >
                {m.signup_social_restart()}
              </Link>
            )}
          </div>
        )}

        <Button type="submit" size="lg" className="w-full gap-2" disabled={loading || !companyName.trim()}>
          {loading ? m.signup_social_submit_loading() : m.signup_social_submit()}
          {!loading && <ArrowRight size={16} />}
        </Button>
      </form>

      <p className="text-center text-xs text-[var(--color-muted-foreground)]">
        {m.signup_privacy_text()}{' '}
        <a
          href="https://getklai.com/docs/legal/privacy"
          className="text-[var(--color-rl-accent-dark)] underline"
        >
          {m.signup_privacy_link()}
        </a>
      </p>
    </AuthPageLayout>
  )
}
