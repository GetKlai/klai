import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { KeyRound } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { API_BASE } from '@/lib/api'

type SearchParams = {
  userID?: string
  code?: string
  orgID?: string
}

export const Route = createFileRoute('/password/set')({
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    // Zitadel sends userId/organization; also accept userID/orgID for consistency
    userID: typeof search.userID === 'string' ? search.userID
          : typeof search.userId === 'string' ? search.userId : undefined,
    code: typeof search.code === 'string' ? search.code : undefined,
    orgID: typeof search.orgID === 'string' ? search.orgID
         : typeof search.organization === 'string' ? search.organization : undefined,
  }),
  component: PasswordSetPage,
})

function PasswordSetPage() {
  const { userID, code } = Route.useSearch()

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (
      password.length < 12 ||
      !Array.from(password).some((char) => /[^\p{L}\p{N}\s]/u.test(char))
    ) {
      setError(m.set_error_min_length())
      return
    }
    if (password !== confirm) {
      setError(m.set_error_mismatch())
      return
    }

    setLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/api/auth/password/set`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userID, code, new_password: password }),
      })

      if (!resp.ok) {
        const data = (await resp.json().catch(() => ({}))) as { detail?: string }
        setError(data?.detail ?? m.set_error_server())
        return
      }

      // Password is set. Backend returns 204 - auto-login is intentionally
      // not attempted here (see #638). Show a success state with an explicit
      // "Inloggen" button; the user clicks through to the standard OIDC
      // login flow at `/` and re-authenticates with the password they just
      // chose. callback.tsx then redirects to /setup/mfa for new users.
      setDone(true)
    } catch {
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
  }

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.set_hero_heading()}
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.set_hero_body()}
      </p>
    </>
  )

  if (!userID || !code) {
    return (
      <AuthPageLayout leftContent={leftContent} showLocale>
        <div className="space-y-3 text-center">
          <p className="text-sm text-[var(--color-destructive-text)]">{m.set_invalid_link()}</p>
          <a href="/" className="block text-xs text-[var(--color-rl-accent-dark)] hover:underline">
            {m.set_invalid_link_back()}
          </a>
        </div>
      </AuthPageLayout>
    )
  }

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      {done ? (
        <div className="space-y-4 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-foreground)]">
            <KeyRound size={22} className="text-[var(--color-rl-cream)]" />
          </div>
          <p className="text-xl font-semibold text-gray-900">
            {m.set_done_heading()}
          </p>
          <p className="text-sm text-gray-400">
            {m.set_done_body()}
          </p>
          {/* Anchor wrapped in Button styling - native browser navigation
              is immune to React event-handler crashes elsewhere on the page
              (e.g. the tabPrompt chunk that throws on /password/set). */}
          <Button asChild size="lg" className="w-full">
            <a href="/">{m.set_done_continue()}</a>
          </Button>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            <h2 className="text-xl font-semibold text-gray-900">
              {m.set_heading()}
            </h2>
            <p className="text-sm text-gray-400">
              {m.set_subheading()}
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="password" className="block text-sm font-medium text-gray-900">
                {m.set_field_password()}
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="new-password"
                autoFocus
                className="w-full rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="confirm" className="block text-sm font-medium text-gray-900">
                {m.set_field_confirm()}
              </label>
              <input
                id="confirm"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                autoComplete="new-password"
                className="w-full rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>

            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}

            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? m.set_submit_loading() : m.set_submit()}
            </Button>
          </form>

          <p className="text-center text-xs text-gray-400">
            <a href="/" className="text-[var(--color-rl-accent-dark)] hover:underline">
              {m.set_back()}
            </a>
          </p>
        </>
      )}
    </AuthPageLayout>
  )
}
