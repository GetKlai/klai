import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, CheckCircle } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { setLocale } from '@/paraglide/runtime'

export const Route = createFileRoute('/signup')({
  component: SignupPage,
})

type Locale = 'nl' | 'en'

function getInitialLocale(): Locale {
  const saved = localStorage.getItem('klai-locale')
  return saved === 'en' ? 'en' : 'nl'
}

interface FormState {
  first_name: string
  last_name: string
  email: string
  password: string
  company_name: string
}

function SignupPage() {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const initial = getInitialLocale()
    setLocale(initial)
    return initial
  })
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

  function switchLocale(l: Locale) {
    setLocale(l)
    setLocaleState(l)
    localStorage.setItem('klai-locale', l)
  }

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
        setError(data?.detail ?? m.signup_error_server({ status: String(resp.status) }))
        return
      }

      setDone(true)
    } catch {
      setError(m.signup_error_connection())
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
            {m.signup_confirm_heading()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.signup_confirm_body({ email: form.email })}
          </p>
          <p className="text-xs text-[var(--color-muted-foreground)] opacity-70">
            {m.signup_confirm_hint()}
          </p>
          <Link
            to="/"
            className="inline-block text-sm font-medium text-[var(--color-purple-muted)] underline"
          >
            {m.signup_confirm_back()}
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      {/* Left panel */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between bg-[var(--color-purple-deep)] p-12 text-[var(--color-sand-light)]">
        <img src="/klai-logo-white.svg" alt="Klai" className="h-8 w-auto" />
        <div className="space-y-4 my-auto">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            {m.signup_hero_heading()}
            <br />
            <span className="text-[var(--color-purple-accent)]">{m.signup_hero_highlight()}</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            {m.signup_hero_body()}
          </p>
        </div>

      </div>

      {/* Right panel — form */}
      <div className="flex w-full flex-col items-center justify-center px-8 py-12 lg:w-1/2">
        <div className="w-full max-w-sm space-y-6">
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

          <div className="space-y-1">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {m.signup_heading()}
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.signup_existing_account()}{' '}
              <Link to="/" className="font-medium text-[var(--color-purple-muted)] underline">
                {m.signup_login_link()}
              </Link>
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Field
                label={m.signup_field_first_name()}
                name="first_name"
                value={form.first_name}
                onChange={(v) => setForm((f) => ({ ...f, first_name: v }))}
                required
              />
              <Field
                label={m.signup_field_last_name()}
                name="last_name"
                value={form.last_name}
                onChange={(v) => setForm((f) => ({ ...f, last_name: v }))}
                required
              />
            </div>
            <Field
              label={m.signup_field_company()}
              name="company_name"
              value={form.company_name}
              onChange={(v) => setForm((f) => ({ ...f, company_name: v }))}
              required
            />
            <Field
              label={m.signup_field_email()}
              name="email"
              type="email"
              value={form.email}
              onChange={(v) => setForm((f) => ({ ...f, email: v }))}
              required
            />
            <Field
              label={m.signup_field_password()}
              name="password"
              type="password"
              value={form.password}
              onChange={(v) => setForm((f) => ({ ...f, password: v }))}
              hint={m.signup_field_password_hint()}
              required
            />

            {error && (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
            )}

            <Button type="submit" size="lg" className="w-full gap-2" disabled={loading}>
              {loading ? m.signup_submit_loading() : m.signup_submit()}
              {!loading && <ArrowRight size={16} />}
            </Button>
          </form>

          <p className="text-center text-xs text-[var(--color-muted-foreground)]">
            {m.signup_privacy_text()}{' '}
            <a href="https://getklai.com/docs/legal/privacy" className="text-[var(--color-purple-muted)] underline">
              {m.signup_privacy_link()}
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
