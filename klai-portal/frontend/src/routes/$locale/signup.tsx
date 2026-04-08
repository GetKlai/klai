import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, CheckCircle } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { useLocale } from '@/lib/locale'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/$locale/signup')({
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
  const { locale } = useLocale()
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const resp = await fetch(`${API_BASE}/api/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, preferred_language: locale }),
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

  if (done) {
    return (
      <AuthPageLayout leftContent={leftContent} showLocale>
        <div className="space-y-4 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-foreground)]">
            <CheckCircle size={22} className="text-[var(--color-rl-cream)]" strokeWidth={1.5} />
          </div>
          <p className="text-base font-semibold text-[var(--color-foreground)]">
            {m.signup_confirm_heading()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.signup_confirm_body({ email: form.email })}
          </p>
          <p className="text-xs text-[var(--color-muted-foreground)] opacity-70">
            {m.signup_confirm_hint()}
          </p>
          <Link
            to="/"
            className="inline-block text-sm font-medium text-[var(--color-rl-accent-dark)] underline"
          >
            {m.signup_confirm_back()}
          </Link>
        </div>
      </AuthPageLayout>
    )
  }

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-1">
        <h2 className="text-base font-semibold text-[var(--color-foreground)]">
          {m.signup_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.signup_existing_account()}{' '}
          <Link to="/" className="font-medium text-[var(--color-rl-accent-dark)] underline">
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
          <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
        )}

        <Button type="submit" size="lg" className="w-full gap-2" disabled={loading}>
          {loading ? m.signup_submit_loading() : m.signup_submit()}
          {!loading && <ArrowRight size={16} />}
        </Button>
      </form>

      <p className="text-center text-xs text-[var(--color-muted-foreground)]">
        {m.signup_privacy_text()}{' '}
        <a href="https://getklai.com/docs/legal/privacy" className="text-[var(--color-rl-accent-dark)] underline">
          {m.signup_privacy_link()}
        </a>
      </p>
    </AuthPageLayout>
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
        className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
      />
      {hint && <p className="text-xs text-[var(--color-muted-foreground)]">{hint}</p>}
    </div>
  )
}
