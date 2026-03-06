import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, CheckCircle } from 'lucide-react'

export const Route = createFileRoute('/signup')({
  component: SignupPage,
})

interface FormState {
  first_name: string
  last_name: string
  email: string
  password: string
  company_name: string
}

function SignupPage() {
  const [form, setForm] = useState<FormState>({
    first_name: '',
    last_name: '',
    email: '',
    password: '',
    company_name: '',
  })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  const apiBase = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const resp = await fetch(`${apiBase}/api/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        setError(data?.detail ?? `Fout ${resp.status} — probeer opnieuw`)
        return
      }

      setDone(true)
    } catch {
      setError('Kan geen verbinding maken met de server')
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="w-full max-w-sm space-y-6 text-center px-6">
          <CheckCircle
            size={48}
            className="mx-auto text-[var(--color-purple-accent)]"
            strokeWidth={1.5}
          />
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            Bevestig je e-mail
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            We hebben een bevestigingslink gestuurd naar <strong>{form.email}</strong>.
            Klik op de link om je account te activeren.
          </p>
          <Link
            to="/"
            className="inline-block text-sm font-medium text-[var(--color-purple-muted)] hover:underline"
          >
            Terug naar inloggen
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      {/* Left panel */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between bg-[var(--color-purple-deep)] p-12 text-[var(--color-sand-light)]">
        <span className="font-serif text-2xl font-bold">Klai</span>
        <div className="space-y-4">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            Jouw eigen AI-werkruimte.
            <br />
            <span className="text-[var(--color-purple-accent)]">Klaar in 2 minuten.</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            Maak een account aan en start direct. Alles draait op Europese servers.
          </p>
        </div>
        <p className="text-xs text-[var(--color-sand-mid)] opacity-50">
          © {new Date().getFullYear()} Klai
        </p>
      </div>

      {/* Right panel — form */}
      <div className="flex w-full flex-col items-center justify-center px-8 py-12 lg:w-1/2">
        <div className="w-full max-w-sm space-y-6">
          <div className="lg:hidden">
            <span className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Klai</span>
          </div>

          <div className="space-y-1">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              Account aanmaken
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              Al een account?{' '}
              <Link to="/" className="font-medium text-[var(--color-purple-muted)] hover:underline">
                Inloggen
              </Link>
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Field
                label="Voornaam"
                name="first_name"
                value={form.first_name}
                onChange={(v) => setForm((f) => ({ ...f, first_name: v }))}
                required
              />
              <Field
                label="Achternaam"
                name="last_name"
                value={form.last_name}
                onChange={(v) => setForm((f) => ({ ...f, last_name: v }))}
                required
              />
            </div>
            <Field
              label="Bedrijfsnaam"
              name="company_name"
              value={form.company_name}
              onChange={(v) => setForm((f) => ({ ...f, company_name: v }))}
              required
            />
            <Field
              label="E-mailadres"
              name="email"
              type="email"
              value={form.email}
              onChange={(v) => setForm((f) => ({ ...f, email: v }))}
              required
            />
            <Field
              label="Wachtwoord"
              name="password"
              type="password"
              value={form.password}
              onChange={(v) => setForm((f) => ({ ...f, password: v }))}
              hint="Minimaal 8 tekens"
              required
            />

            {error && (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
            )}

            <Button type="submit" size="lg" className="w-full gap-2" disabled={loading}>
              {loading ? 'Even geduld…' : 'Account aanmaken'}
              {!loading && <ArrowRight size={16} />}
            </Button>
          </form>

          <p className="text-center text-xs text-[var(--color-muted-foreground)] opacity-60">
            Door aan te melden ga je akkoord met onze{' '}
            <a href="https://getklai.com/privacy" className="hover:underline">
              privacyverklaring
            </a>
          </p>
        </div>
      </div>
    </div>
  )
}

function Field({
  label,
  name,
  type = 'text',
  value,
  onChange,
  hint,
  required,
}: {
  label: string
  name: string
  type?: string
  value: string
  onChange: (v: string) => void
  hint?: string
  required?: boolean
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={name} className="block text-sm font-medium text-[var(--color-foreground)]">
        {label}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
      />
      {hint && <p className="text-xs text-[var(--color-muted-foreground)]">{hint}</p>}
    </div>
  )
}
