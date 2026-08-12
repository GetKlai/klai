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
  // SPEC-AUTH-010 R1: existing workspace(s) for the IdP-verified email domain.
  const [domainMatch, setDomainMatch] = useState<{
    domain: string
    orgs: { org_id: number; name: string; auto_accept: boolean }[]
  } | null>(null)
  // SPEC-AUTH-010 C1.3: explicit "start a separate workspace" escape hatch.
  const [createNewWorkspace, setCreateNewWorkspace] = useState(false)
  // SPEC-AUTH-010 R5: founder's auto-accept choice (default on).
  const [autoAccept, setAutoAccept] = useState(true)
  const emailDomain = email.includes('@') ? email.split('@').pop() ?? '' : ''

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
        body: JSON.stringify({
          company_name: companyName.trim(),
          create_new_workspace: createNewWorkspace,
          auto_accept_same_domain: autoAccept,
        }),
      })

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        if (resp.status === 400 && data?.detail?.toLowerCase().includes('expired')) {
          setError(m.signup_social_expired())
        } else if (resp.status === 409) {
          setError(m.signup_social_name_taken())
        } else if (typeof data?.detail === 'string' && data.detail.trim()) {
          setError(data.detail)
        } else {
          setError(m.signup_social_error_server({ status: String(resp.status) }))
        }
        return
      }

      const data = await resp.json()
      // SPEC-AUTH-010 R1: existing workspace(s) for this domain — offer to join.
      if (data?.kind === 'domain_match') {
        setDomainMatch({ domain: data.domain, orgs: data.orgs ?? [] })
        return
      }
      // SSO cookie is set by the backend - navigate to root to trigger OIDC auto-login
      window.location.href = data.redirect_url ?? '/'
    } catch {
      setError(m.signup_error_connection())
    } finally {
      setLoading(false)
    }
  }

  // SPEC-AUTH-010 R2: join a domain-match workspace (auto-join or request).
  async function handleJoin(orgId: number) {
    setError(null)
    setLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/api/signup/social/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ org_id: orgId }),
      })
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        if (resp.status === 400 && data?.detail?.toLowerCase().includes('expired')) {
          setError(m.signup_social_expired())
        } else if (typeof data?.detail === 'string' && data.detail.trim()) {
          setError(data.detail)
        } else {
          setError(m.signup_social_error_server({ status: String(resp.status) }))
        }
        return
      }
      const data = await resp.json()
      const target: string = data.kind === 'auto_join' ? (data.redirect_url ?? '/') : (data.redirect_to ?? '/')
      window.location.replace(target)
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

  // SPEC-AUTH-010 R1/C1.4: domain-match view — join a workspace or escape to
  // creating a separate one.
  if (domainMatch) {
    return (
      <AuthPageLayout leftContent={leftContent} showLocale>
        <div className="space-y-2">
          <h2 className="text-xl font-semibold text-gray-900">
            {m.social_domain_match_heading()}
          </h2>
          <p className="text-sm text-gray-400">
            {m.social_domain_match_body({ domain: domainMatch.domain })}
          </p>
        </div>

        <div className="space-y-2">
          {domainMatch.orgs.map((org) => (
            <div
              key={org.org_id}
              className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-[var(--color-card)] p-4"
            >
              <div>
                <span className="block font-medium text-gray-900">{org.name}</span>
                <span className="block text-xs text-gray-400">
                  {org.auto_accept
                    ? m.select_workspace_join_auto_hint()
                    : m.select_workspace_request_hint()}
                </span>
              </div>
              <Button size="sm" disabled={loading} onClick={() => void handleJoin(org.org_id)}>
                {org.auto_accept ? m.social_join_auto_cta() : m.social_join_request_cta()}
              </Button>
            </div>
          ))}
        </div>

        {error && (
          <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">
            {error}
          </p>
        )}

        <p className="text-center text-xs text-gray-400">
          <button
            type="button"
            onClick={() => {
              setCreateNewWorkspace(true)
              setDomainMatch(null)
              setError(null)
            }}
            className="text-[var(--color-rl-accent-dark)] hover:underline"
          >
            {m.signup_domain_match_create_cta()}
          </button>
        </p>
      </AuthPageLayout>
    )
  }

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-1">
        <h2 className="text-xl font-semibold text-gray-900">
          {m.signup_social_heading()}
        </h2>
        <p className="text-sm text-gray-400">
          {m.signup_social_subheading()}
        </p>
      </div>

      {/* Identity confirmation - read-only */}
      <div className="rounded-lg border border-gray-200 bg-[var(--color-muted)] px-3 py-2.5">
        <p className="text-xs text-gray-400">
          {m.signup_social_identity_label()}
        </p>
        <p className="text-sm font-medium text-gray-900">{displayName}</p>
        {displayName !== email && (
          <p className="text-xs text-gray-400">{email}</p>
        )}
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1">
          <label
            htmlFor="company_name"
            className="block text-sm font-medium text-gray-900"
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
            className="w-full rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
          />
        </div>

        {/* SPEC-AUTH-010 R5: founder's auto-accept choice (server-side guarded). */}
        {emailDomain && (
          <label className="flex items-start gap-2 text-sm text-gray-900">
            <input
              type="checkbox"
              checked={autoAccept}
              onChange={(e) => setAutoAccept(e.target.checked)}
              className="mt-0.5 accent-[var(--color-rl-accent)]"
            />
            <span>
              {m.signup_auto_accept_label({ domain: emailDomain })}
              <span className="block text-xs text-gray-400">{m.signup_auto_accept_hint()}</span>
            </span>
          </label>
        )}

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

      <p className="text-center text-xs text-gray-400">
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
