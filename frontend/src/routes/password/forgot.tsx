import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Mail } from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export const Route = createFileRoute('/password/forgot')({
  component: ForgotPasswordPage,
})

function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
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
            Wachtwoord vergeten?
            <br />
            <span className="text-[var(--color-purple-accent)]">Geen probleem.</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            Vul je e-mailadres in en we sturen je een link om je wachtwoord opnieuw in te stellen.
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
                <Mail size={22} className="text-[var(--color-sand-light)]" />
              </div>
              <p className="font-serif text-xl font-bold text-[var(--color-purple-deep)]">
                Mail onderweg
              </p>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                Als dit e-mailadres bij ons bekend is, ontvang je een link om je wachtwoord opnieuw in te stellen.
              </p>
              <a href="/" className="block text-xs text-[var(--color-purple-muted)] hover:underline pt-2">
                Terug naar inloggen
              </a>
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
                  Wachtwoord vergeten
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  Vul je e-mailadres in en we sturen je een resetlink.
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

                {error && (
                  <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
                )}

                <Button type="submit" size="lg" className="w-full" disabled={loading}>
                  {loading ? 'Versturen…' : 'Resetlink sturen'}
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
