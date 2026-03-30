import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Mail } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { API_BASE } from '@/lib/api'

type SearchParams = {
  email?: string
}

export const Route = createFileRoute('/$locale/password/forgot')({
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    email: typeof search.email === 'string' ? search.email : undefined,
  }),
  component: ForgotPasswordPage,
})

function ForgotPasswordPage() {
  const { email: emailParam } = Route.useSearch()

  const [email, setEmail] = useState(emailParam ?? '')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      await fetch(`${API_BASE}/api/auth/password/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      // Always show confirmation — do not reveal whether email exists
      setDone(true)
    } catch {
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
  }

  const leftContent = (
    <>
      <h1 className="font-serif text-4xl font-bold leading-tight">
        {m.forgot_hero_heading()}
        <br />
        <span className="text-[var(--color-purple-accent)]">{m.forgot_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
        {m.forgot_hero_body()}
      </p>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      {done ? (
        <div className="space-y-3 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-purple-deep)]">
            <Mail size={22} className="text-[var(--color-sand-light)]" />
          </div>
          <p className="font-serif text-xl font-bold text-[var(--color-purple-deep)]">
            {m.forgot_done_heading()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.forgot_done_body()}
          </p>
          <a href="/" className="block text-xs text-[var(--color-purple-muted)] hover:underline pt-2">
            {m.forgot_back()}
          </a>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {m.forgot_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.forgot_subheading()}
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="email" className="block text-sm font-medium text-[var(--color-foreground)]">
                {m.forgot_field_email()}
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

            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}

            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? m.forgot_submit_loading() : m.forgot_submit()}
            </Button>
          </form>

          <p className="text-center text-xs text-[var(--color-muted-foreground)]">
            <a href="/" className="text-[var(--color-purple-muted)] hover:underline">
              {m.forgot_back()}
            </a>
          </p>
        </>
      )}
    </AuthPageLayout>
  )
}
