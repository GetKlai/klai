import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Mail } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { setLocale } from '@/paraglide/runtime'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

type Locale = 'nl' | 'en'

function getInitialLocale(): Locale {
  const saved = localStorage.getItem('klai-locale')
  return saved === 'en' ? 'en' : 'nl'
}

type SearchParams = {
  email?: string
}

export const Route = createFileRoute('/password/forgot')({
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    email: typeof search.email === 'string' ? search.email : undefined,
  }),
  component: ForgotPasswordPage,
})

function ForgotPasswordPage() {
  const { email: emailParam } = Route.useSearch()

  const [locale, setLocaleState] = useState<Locale>(() => {
    const initial = getInitialLocale()
    setLocale(initial)
    return initial
  })

  function switchLocale(l: Locale) {
    setLocale(l)
    setLocaleState(l)
    localStorage.setItem('klai-locale', l)
  }

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
      setError(m.forgot_error_connection())
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
        <div className="space-y-4 my-auto">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            {m.forgot_hero_heading()}
            <br />
            <span className="text-[var(--color-purple-accent)]">{m.forgot_hero_highlight()}</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            {m.forgot_hero_body()}
          </p>
        </div>

      </div>

      {/* Right panel — form */}
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
                  <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
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
        </div>
      </div>
    </div>
  )
}
