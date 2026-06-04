import { createFileRoute, Link } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, CheckCircle } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { useLocale } from '@/lib/locale'
import { API_BASE } from '@/lib/api'
import { PasswordStrengthMeter } from '@/components/auth/PasswordStrengthMeter'
import {
  evaluateSignupPassword,
  estimateSignupPasswordStrength,
  type SignupPasswordStrength,
} from '@/lib/password-strength'
import { passwordPolicyIssueMessage } from '@/lib/password-policy-copy'

export const Route = createFileRoute('/$locale/signup/')({
  component: SignupPage,
})

interface FormState {
  first_name: string
  last_name: string
  email: string
  password: string
  company_name: string
}

// SPEC-LAUNCH-SOFTLAUNCH-001 B-2: read the invite token from ?token=...
// in the URL exactly once on mount. We do not parse the payload client-side
// (it's opaque to the frontend); we just forward it to /api/signup which
// verifies the HMAC and the email-match. Pre-filling email + company is a
// separate query param so the bare URL is human-readable without any
// payload decoding.
function readInviteParamsFromUrl() {
  if (typeof window === 'undefined') return { token: null, email: '', company: '' }
  const params = new URLSearchParams(window.location.search)
  return {
    token: params.get('token'),
    email: params.get('email') ?? '',
    company: params.get('company') ?? '',
  }
}

function SignupPage() {
  const { locale } = useLocale()
  const invite = useMemo(() => readInviteParamsFromUrl(), [])
  const [form, setForm] = useState<FormState>({
    first_name: '',
    last_name: '',
    email: invite.email,
    password: '',
    company_name: invite.company,
  })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)
  const passwordInputs = useMemo(
    () => [form.email, form.first_name, form.last_name, form.company_name, 'Klai'],
    [form.company_name, form.email, form.first_name, form.last_name],
  )
  const [passwordStrength, setPasswordStrength] = useState<SignupPasswordStrength>(() =>
    estimateSignupPasswordStrength(''),
  )

  useEffect(() => {
    let cancelled = false
    setPasswordStrength(estimateSignupPasswordStrength(form.password))
    if (!form.password) return

    void evaluateSignupPassword(form.password, passwordInputs).then((result) => {
      if (!cancelled) setPasswordStrength(result)
    })

    return () => {
      cancelled = true
    }
  }, [form.password, passwordInputs])

  async function handleSocialSignup(idpId: string) {
    setError(null)
    setLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/api/auth/idp-intent-signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idp_id: idpId,
          locale,
          ...(invite.token ? { invite_token: invite.token } : {}),
        }),
      })
      if (!resp.ok) {
        setError(m.signup_error_server({ status: String(resp.status) }))
        setLoading(false)
        return
      }
      const data = await resp.json()
      window.location.href = data.auth_url
    } catch {
      setError(m.signup_error_connection())
      setLoading(false)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const latestPasswordStrength = await evaluateSignupPassword(form.password, passwordInputs)
    setPasswordStrength(latestPasswordStrength)
    if (!latestPasswordStrength.isAcceptable) {
      setError(passwordPolicyIssueMessage(latestPasswordStrength.issues[0]))
      return
    }

    setLoading(true)

    try {
      const resp = await fetch(`${API_BASE}/api/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          preferred_language: locale,
          // SPEC-LAUNCH-SOFTLAUNCH-001 B-2: forward invite token if present.
          // Backend verifies HMAC + email match; absence is normal for
          // unsolicited signups (those still go through the free-email block).
          ...(invite.token ? { invite_token: invite.token } : {}),
        }),
      })

      if (!resp.ok) {
        if (resp.status === 429) {
          setError(m.signup_error_rate_limited())
          return
        }
        const data = await resp.json().catch(() => ({}))
        setError(readSignupError(data, resp.status))
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
          <p className="text-xl font-semibold text-gray-900">
            {m.signup_confirm_heading()}
          </p>
          <p className="text-sm text-gray-400">
            {m.signup_confirm_body({ email: form.email })}
          </p>
          <p className="text-xs text-gray-400 opacity-70">
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
        <h2 className="text-xl font-semibold text-gray-900">
          {m.signup_heading()}
        </h2>
        <p className="text-sm text-gray-400">
          {m.signup_existing_account()}{' '}
          <Link to="/" className="font-medium text-[var(--color-rl-accent-dark)] underline">
            {m.signup_login_link()}
          </Link>
        </p>
      </div>

      {/* Social signup */}
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => handleSocialSignup('google')}
          disabled={loading}
          className="flex w-full items-center justify-center gap-3 rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-sm font-medium text-gray-900 transition hover:bg-[var(--color-muted)] disabled:opacity-50"
        >
          {/* Google G */}
          <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
            <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
            <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
            <path d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332z" fill="#FBBC05"/>
            <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.962L3.964 7.294C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
          </svg>
          {m.signup_with_google()}
        </button>

        <button
          type="button"
          onClick={() => handleSocialSignup('microsoft')}
          disabled={loading}
          className="flex w-full items-center justify-center gap-3 rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-sm font-medium text-gray-900 transition hover:bg-[var(--color-muted)] disabled:opacity-50"
        >
          {/* Microsoft squares */}
          <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
            <path d="M0 0h8.571v8.571H0z" fill="#F25022"/>
            <path d="M9.429 0H18v8.571H9.429z" fill="#7FBA00"/>
            <path d="M0 9.429h8.571V18H0z" fill="#00A4EF"/>
            <path d="M9.429 9.429H18V18H9.429z" fill="#FFB900"/>
          </svg>
          {m.signup_with_microsoft()}
        </button>
      </div>

      <div className="relative flex items-center gap-3">
        <div className="h-px flex-1 bg-gray-200" />
        <span className="text-xs text-gray-400">{m.signup_or_continue_with()}</span>
        <div className="h-px flex-1 bg-gray-200" />
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
        <PasswordStrengthMeter
          score={passwordStrength.score}
          issues={passwordStrength.issues}
          show={form.password.length > 0}
        />

        {error && (
          <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
        )}

        <Button type="submit" size="lg" className="w-full gap-2" disabled={loading}>
          {loading ? m.signup_submit_loading() : m.signup_submit()}
          {!loading && <ArrowRight size={16} />}
        </Button>
      </form>

      <p className="text-center text-xs text-gray-400">
        {m.signup_privacy_text()}{' '}
        <a href="https://getklai.com/docs/legal/privacy" className="text-[var(--color-rl-accent-dark)] underline">
          {m.signup_privacy_link()}
        </a>
      </p>
    </AuthPageLayout>
  )
}

function readSignupError(data: unknown, status: number) {
  const detail = typeof data === 'object' && data !== null && 'detail' in data ? data.detail : null
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const firstPasswordError = detail.find((item) => {
      if (typeof item !== 'object' || item === null) return false
      const loc = 'loc' in item && Array.isArray(item.loc) ? item.loc : []
      const msg = 'msg' in item && typeof item.msg === 'string' ? item.msg : ''
      return loc.includes('password') || msg.toLowerCase().includes('wachtwoord') || msg.toLowerCase().includes('password')
    })
    if (typeof firstPasswordError === 'object' && firstPasswordError !== null && 'msg' in firstPasswordError) {
      const msg = firstPasswordError.msg
      if (typeof msg === 'string' && msg.trim()) return msg
    }
  }
  return m.signup_error_server({ status: String(status) })
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
      <label htmlFor={name} className="block text-sm font-medium text-gray-900">
        {label}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        className="w-full rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
      />
      {hint && <p className="text-xs text-gray-400">{hint}</p>}
    </div>
  )
}
