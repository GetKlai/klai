import { createFileRoute, useNavigate } from '@tanstack/react-router'
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
  const navigate = useNavigate()

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (password.length < 8) {
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
        const data = await resp.json().catch(() => ({}))
        setError(data?.detail ?? m.set_error_server())
        return
      }

      setDone(true)
      setTimeout(() => navigate({ to: '/' }), 2500)
    } catch {
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
  }

  const leftContent = (
    <>
      <h1 className="font-serif text-4xl font-bold leading-tight">
        {m.set_hero_heading()}
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
        {m.set_hero_body()}
      </p>
    </>
  )

  if (!userID || !code) {
    return (
      <AuthPageLayout leftContent={leftContent} showLocale>
        <div className="space-y-3 text-center">
          <p className="text-sm text-[var(--color-destructive-text)]">{m.set_invalid_link()}</p>
          <a href="/" className="block text-xs text-[var(--color-purple-muted)] hover:underline">
            {m.set_invalid_link_back()}
          </a>
        </div>
      </AuthPageLayout>
    )
  }

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      {done ? (
        <div className="space-y-3 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-purple-deep)]">
            <KeyRound size={22} className="text-[var(--color-sand-light)]" />
          </div>
          <p className="font-serif text-xl font-bold text-[var(--color-purple-deep)]">
            {m.set_done_heading()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.set_done_body()}
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {m.set_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.set_subheading()}
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="password" className="block text-sm font-medium text-[var(--color-foreground)]">
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
                className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="confirm" className="block text-sm font-medium text-[var(--color-foreground)]">
                {m.set_field_confirm()}
              </label>
              <input
                id="confirm"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                autoComplete="new-password"
                className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
              />
            </div>

            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}

            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? m.set_submit_loading() : m.set_submit()}
            </Button>
          </form>

          <p className="text-center text-xs text-[var(--color-muted-foreground)]">
            <a href="/" className="text-[var(--color-purple-muted)] hover:underline">
              {m.set_back()}
            </a>
          </p>
        </>
      )}
    </AuthPageLayout>
  )
}
