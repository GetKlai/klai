import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, Lock, Shield } from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

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

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // If Zitadel didn't supply an authRequestId, the user arrived here directly.
  // Send them back to / so signinRedirect() can start the OIDC flow properly.
  if (!authRequestId) {
    navigate({ to: '/' })
    return null
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
        setError(data?.detail ?? 'Inloggen mislukt, probeer het later opnieuw')
        return
      }

      const { callback_url } = await resp.json()
      // Navigate to the OIDC callback URL — react-oidc-context picks it up from there
      window.location.href = callback_url
    } catch {
      setError('Geen verbinding, controleer je internetverbinding')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      {/* Left panel — branding */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between bg-[var(--color-purple-deep)] p-12 text-[var(--color-sand-light)]">
        <div>
          <span className="font-serif text-2xl font-bold">Klai</span>
        </div>

        <div className="space-y-6">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            AI die van jou is.
            <br />
            <span className="text-[var(--color-purple-accent)]">Niet van Silicon Valley.</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            Jouw werkruimte. Jouw data. Draait op Europese servers, nooit gedeeld, altijd aantoonbaar privé.
          </p>

          <div className="flex flex-col gap-3 pt-4">
            <div className="flex items-center gap-3 text-sm text-[var(--color-sand-mid)]">
              <Shield size={16} className="shrink-0 text-[var(--color-purple-accent)]" />
              Europese infrastructuur — data verlaat nooit de EU
            </div>
            <div className="flex items-center gap-3 text-sm text-[var(--color-sand-mid)]">
              <Lock size={16} className="shrink-0 text-[var(--color-purple-accent)]" />
              Open source modellen, aantoonbaar privé
            </div>
          </div>
        </div>

        <p className="text-xs text-[var(--color-sand-mid)] opacity-50">
          © {new Date().getFullYear()} Klai
        </p>
      </div>

      {/* Right panel — login form */}
      <div className="flex w-full flex-col items-center justify-center px-8 lg:w-1/2">
        <div className="w-full max-w-sm space-y-8">
          {/* Mobile logo */}
          <div className="lg:hidden">
            <span className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Klai</span>
          </div>

          <div className="space-y-2">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              Welkom terug
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              Log in op jouw werkruimte
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="email" className="block text-sm font-medium text-[var(--color-foreground)]">
                E-mailadres
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
                Wachtwoord
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
              {loading ? 'Inloggen…' : 'Inloggen'}
              {!loading && <ArrowRight size={16} />}
            </Button>
          </form>

          <p className="text-center text-xs text-[var(--color-muted-foreground)] opacity-60">
            Door in te loggen ga je akkoord met onze{' '}
            <a href="https://getklai.com/privacy" className="hover:underline">
              privacyverklaring
            </a>
          </p>
        </div>
      </div>
    </div>
  )
}
