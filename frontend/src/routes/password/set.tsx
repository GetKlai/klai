import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { KeyRound } from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

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

  if (!userID || !code) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="space-y-3 text-center max-w-sm px-4">
          <p className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Klai</p>
          <p className="text-sm text-red-700">Deze link is ongeldig of verlopen.</p>
          <a href="/" className="block text-xs text-[var(--color-purple-muted)] hover:underline">
            Terug naar inloggen
          </a>
        </div>
      </div>
    )
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (password.length < 8) {
      setError('Wachtwoord moet minimaal 8 tekens bevatten')
      return
    }
    if (password !== confirm) {
      setError('Wachtwoorden komen niet overeen')
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
        setError(data?.detail ?? 'Wachtwoord instellen mislukt, probeer het later opnieuw')
        return
      }

      setDone(true)
      setTimeout(() => navigate({ to: '/' }), 2500)
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
        <div className="space-y-4">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            Bijna klaar.
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            Kies een sterk wachtwoord om je account te beveiligen.
          </p>
        </div>
        <p className="text-xs text-[var(--color-sand-mid)] opacity-50">
          © {new Date().getFullYear()} Klai
        </p>
      </div>

      {/* Right panel — form */}
      <div className="flex w-full flex-col items-center justify-center px-8 lg:w-1/2">
        <div className="w-full max-w-sm space-y-8">
          {/* Mobile logo */}
          <div className="lg:hidden">
            <span className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Klai</span>
          </div>

          {done ? (
            <div className="space-y-3 text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-purple-deep)]">
                <KeyRound size={22} className="text-[var(--color-sand-light)]" />
              </div>
              <p className="font-serif text-xl font-bold text-[var(--color-purple-deep)]">
                Wachtwoord ingesteld
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                Je wordt doorgestuurd naar inloggen…
              </p>
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
                  Nieuw wachtwoord
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  Kies een wachtwoord van minimaal 8 tekens.
                </p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-1">
                  <label htmlFor="password" className="block text-sm font-medium text-[var(--color-foreground)]">
                    Nieuw wachtwoord
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
                    Bevestig wachtwoord
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
                  <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
                )}

                <Button type="submit" size="lg" className="w-full" disabled={loading}>
                  {loading ? 'Opslaan…' : 'Wachtwoord instellen'}
                </Button>
              </form>

              <p className="text-center text-xs text-[var(--color-muted-foreground)]">
                <a href="/" className="text-[var(--color-purple-muted)] hover:underline">
                  Terug naar inloggen
                </a>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
