import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { AlertCircle, CheckCircle, CreditCard, ExternalLink, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/admin/billing')({
  component: BillingPage,
})

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

// --- Types ---

type Plan = 'core' | 'professional' | 'complete' | 'free'
type BillingCycle = 'monthly' | 'yearly'
type BillingStatus = 'pending' | 'mandate_requested' | 'active' | 'payment_failed' | 'cancelled'

interface BillingStatusResponse {
  billing_status: BillingStatus
  plan: Plan
  billing_cycle: BillingCycle
  seats: number
  moneybird_contact_id: string | null
}

interface MandateForm {
  plan: Plan
  billing_cycle: BillingCycle
  seats: number
  address: string
  zipcode: string
  city: string
  country: string
  tax_number: string
  chamber_of_commerce: string
  billing_email: string
  internal_reference: string
}

// --- Plan definitions ---

const PLANS: { id: Plan; name: string; description: string; monthly: number; yearly: number }[] = [
  {
    id: 'core',
    name: 'Chat',
    description: 'AI-chat voor je team',
    monthly: 22,
    yearly: 18,
  },
  {
    id: 'professional',
    name: 'Chat + Focus',
    description: 'Chat en productiviteitstools',
    monthly: 42,
    yearly: 34,
  },
  {
    id: 'complete',
    name: 'Chat + Focus + Scribe',
    description: 'Volledig AI-platform',
    monthly: 60,
    yearly: 48,
  },
]

const PLAN_LABELS: Record<Plan, string> = {
  core: 'Chat',
  professional: 'Chat + Focus',
  complete: 'Chat + Focus + Scribe',
  free: 'Intern account',
}

const CYCLE_LABELS: Record<BillingCycle, string> = {
  monthly: 'Maandelijks',
  yearly: 'Jaarlijks',
}

// --- Helpers ---

function planPrice(plan: Plan, cycle: BillingCycle): number {
  const p = PLANS.find((p) => p.id === plan)!
  return cycle === 'yearly' ? p.yearly : p.monthly
}

function totalPrice(plan: Plan, cycle: BillingCycle, seats: number): string {
  const price = planPrice(plan, cycle) * seats
  return cycle === 'yearly'
    ? `\u20ac${(price * 12).toLocaleString('nl-NL')} / jaar`
    : `\u20ac${price} / maand`
}

// --- Field component ---

function Field({
  label,
  name,
  type = 'text',
  value,
  onChange,
  hint,
  required,
  placeholder,
}: {
  label: string
  name: string
  type?: string
  value: string
  onChange: (v: string) => void
  hint?: string
  required?: boolean
  placeholder?: string
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={name} className="block text-sm font-medium text-[var(--color-foreground)]">
        {label}
        {!required && (
          <span className="ml-1 text-xs text-[var(--color-muted-foreground)]">(optioneel)</span>
        )}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        placeholder={placeholder}
        className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
      />
      {hint && <p className="text-xs text-[var(--color-muted-foreground)]">{hint}</p>}
    </div>
  )
}

// --- Main page ---

function BillingPage() {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''

  const [billingStatus, setBillingStatus] = useState<BillingStatusResponse | null>(null)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)

  useEffect(() => {
    if (!token) return
    fetch(`${API_BASE}/api/billing/status`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.json())
      .then(setBillingStatus)
      .catch(() => setFetchError('Kon factuurstatus niet ophalen'))
      .finally(() => setLoadingStatus(false))
  }, [token])

  if (loadingStatus) {
    return (
      <div className="p-8">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Abonnement</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Plan, facturering en betaalhistorie.
        </p>
      </div>

      {fetchError && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle size={16} className="shrink-0" />
          {fetchError}
        </div>
      )}

      {billingStatus && (
        <>
          {billingStatus.plan === 'free' && <FreeView />}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'pending' && (
            <SetupView token={token} onComplete={setBillingStatus} />
          )}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'mandate_requested' && <MandateRequestedView />}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'active' && (
            <ActiveView token={token} status={billingStatus} onCancel={setBillingStatus} />
          )}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'payment_failed' && (
            <PaymentFailedView
              onRetry={() => setBillingStatus({ ...billingStatus, billing_status: 'pending' })}
            />
          )}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'cancelled' && (
            <CancelledView
              onReactivate={() => setBillingStatus({ ...billingStatus, billing_status: 'pending' })}
            />
          )}
        </>
      )}
    </div>
  )
}

// --- State: pending ---

function SetupView({
  token,
  onComplete,
}: {
  token: string
  onComplete: (s: BillingStatusResponse) => void
}) {
  const [form, setForm] = useState<MandateForm>({
    plan: 'professional',
    billing_cycle: 'monthly',
    seats: 1,
    address: '',
    zipcode: '',
    city: '',
    country: 'NL',
    tax_number: '',
    chamber_of_commerce: '',
    billing_email: '',
    internal_reference: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = <K extends keyof MandateForm>(key: K, val: MandateForm[K]) =>
    setForm((f) => ({ ...f, [key]: val }))

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const body: Record<string, unknown> = {
        plan: form.plan,
        billing_cycle: form.billing_cycle,
        seats: form.seats,
        address: form.address,
        zipcode: form.zipcode,
        city: form.city,
        country: form.country,
      }
      if (form.tax_number) body.tax_number = form.tax_number
      if (form.chamber_of_commerce) body.chamber_of_commerce = form.chamber_of_commerce
      if (form.billing_email) body.billing_email = form.billing_email
      if (form.internal_reference) body.internal_reference = form.internal_reference

      const res = await fetch(`${API_BASE}/api/billing/mandate`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data?.detail ?? `Fout ${res.status} — probeer opnieuw`)
        return
      }

      const data = await res.json()

      if (data.mandate_url) {
        window.location.href = data.mandate_url
      } else {
        // Moneybird Payments not yet active — billing_status is already "mandate_requested" in DB
        onComplete({
          billing_status: 'mandate_requested',
          plan: form.plan,
          billing_cycle: form.billing_cycle,
          seats: form.seats,
          moneybird_contact_id: null,
        })
      }
    } catch {
      setError('Kan geen verbinding maken met de server')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Plan + cycle selection */}
      <Card>
        <CardHeader>
          <CardTitle>Kies een plan</CardTitle>
          <CardDescription>Prijzen per gebruiker per maand, excl. BTW.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Cycle toggle */}
          <div className="flex gap-2">
            {(['monthly', 'yearly'] as BillingCycle[]).map((cycle) => (
              <button
                key={cycle}
                type="button"
                onClick={() => set('billing_cycle', cycle)}
                className={[
                  'flex-1 rounded-lg border px-4 py-2 text-sm font-medium transition',
                  form.billing_cycle === cycle
                    ? 'border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-deep)]'
                    : 'border-[var(--color-border)] text-[var(--color-muted-foreground)] hover:border-[var(--color-purple-muted)]',
                ].join(' ')}
              >
                {CYCLE_LABELS[cycle]}
                {cycle === 'yearly' && (
                  <span className="ml-2 text-xs text-[var(--color-purple-accent)]">20% korting</span>
                )}
              </button>
            ))}
          </div>

          {/* Plan tiles */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {PLANS.map((plan) => (
              <button
                key={plan.id}
                type="button"
                onClick={() => set('plan', plan.id)}
                className={[
                  'flex flex-col items-start rounded-xl border p-4 text-left transition',
                  form.plan === plan.id
                    ? 'border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/10'
                    : 'border-[var(--color-border)] hover:border-[var(--color-purple-muted)]',
                ].join(' ')}
              >
                <span className="text-sm font-semibold text-[var(--color-purple-deep)]">
                  {plan.name}
                </span>
                <span className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
                  {plan.description}
                </span>
                <span className="mt-3 text-lg font-bold text-[var(--color-purple-deep)]">
                  &euro;{form.billing_cycle === 'yearly' ? plan.yearly : plan.monthly}
                  <span className="text-xs font-normal text-[var(--color-muted-foreground)]">
                    {' '}
                    /gebr/mnd
                  </span>
                </span>
              </button>
            ))}
          </div>

          {/* Seats + total */}
          <div className="flex items-end gap-4 pt-1 border-t border-[var(--color-border)]">
            <div className="space-y-1">
              <label
                htmlFor="seats"
                className="block text-sm font-medium text-[var(--color-foreground)]"
              >
                Aantal gebruikers
              </label>
              <input
                id="seats"
                type="number"
                min={1}
                max={500}
                value={form.seats}
                onChange={(e) => set('seats', Math.max(1, parseInt(e.target.value) || 1))}
                className="w-24 rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-sm outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
              />
            </div>
            <div className="ml-auto text-right">
              <p className="text-xs text-[var(--color-muted-foreground)]">Totaal excl. BTW</p>
              <p className="text-xl font-bold text-[var(--color-purple-deep)]">
                {totalPrice(form.plan, form.billing_cycle, form.seats)}
              </p>
              {form.billing_cycle === 'yearly' && (
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  &euro;{planPrice(form.plan, form.billing_cycle) * form.seats} /mnd equivalent
                </p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Billing details */}
      <Card>
        <CardHeader>
          <CardTitle>Factuurgegevens</CardTitle>
          <CardDescription>
            Verplicht voor een rechtsgeldige Nederlandse factuur (Wet OB 1968, art. 35).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field
            label="Straat en huisnummer"
            name="address"
            value={form.address}
            onChange={(v) => set('address', v)}
            required
            placeholder="Voorbeeldstraat 1"
          />
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="Postcode"
              name="zipcode"
              value={form.zipcode}
              onChange={(v) => set('zipcode', v)}
              required
              placeholder="1234 AB"
            />
            <Field
              label="Stad"
              name="city"
              value={form.city}
              onChange={(v) => set('city', v)}
              required
              placeholder="Amsterdam"
            />
          </div>
          <Field
            label="Land"
            name="country"
            value={form.country}
            onChange={(v) => set('country', v)}
            required
          />
          <Field
            label="BTW-nummer"
            name="tax_number"
            value={form.tax_number}
            onChange={(v) => set('tax_number', v)}
            placeholder="NL123456789B01"
          />
          <Field
            label="KvK-nummer"
            name="chamber_of_commerce"
            value={form.chamber_of_commerce}
            onChange={(v) => set('chamber_of_commerce', v)}
          />
          <Field
            label="Facturatie-e-mailadres"
            name="billing_email"
            type="email"
            value={form.billing_email}
            onChange={(v) => set('billing_email', v)}
            hint="Als facturen naar een ander adres moeten dan je inlogadres."
          />
          <Field
            label="Interne referentie / PO-nummer"
            name="internal_reference"
            value={form.internal_reference}
            onChange={(v) => set('internal_reference', v)}
            hint="Wordt vermeld op de factuur."
          />
        </CardContent>
      </Card>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle size={16} className="shrink-0" />
          {error}
        </div>
      )}

      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-[var(--color-muted-foreground)]">
          Je wordt doorgestuurd naar Moneybird voor de SEPA-betaalmachtiging.
        </p>
        <Button type="submit" disabled={loading} className="shrink-0 gap-2">
          <CreditCard size={16} />
          {loading ? 'Even geduld\u2026' : 'Start abonnement'}
        </Button>
      </div>
    </form>
  )
}

// --- Plan: free ---

function FreeView() {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-[var(--color-purple-accent)]/10 p-4">
          <CheckCircle size={24} className="text-[var(--color-purple-accent)]" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-purple-deep)]">Intern account</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            Dit account heeft gratis toegang tot het volledige Klai-platform.
          </p>
        </div>
        <Badge variant="secondary">Gratis</Badge>
      </CardContent>
    </Card>
  )
}

// --- State: mandate_requested ---

function MandateRequestedView() {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-[var(--color-purple-accent)]/10 p-4">
          <CreditCard size={24} className="text-[var(--color-purple-accent)]" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-purple-deep)]">Aanvraag ontvangen</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            We hebben je gegevens ontvangen en nemen contact met je op om de betaling in te stellen.
            Je ontvangt een bevestiging zodra je abonnement actief is.
          </p>
        </div>
        <Badge variant="secondary">In behandeling</Badge>
      </CardContent>
    </Card>
  )
}

// --- State: active ---

function ActiveView({
  token,
  status,
  onCancel,
}: {
  token: string
  status: BillingStatusResponse
  onCancel: (s: BillingStatusResponse) => void
}) {
  const [loadingInvoices, setLoadingInvoices] = useState(false)
  const [cancelConfirm, setCancelConfirm] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function openInvoicePortal() {
    setLoadingInvoices(true)
    setActionError(null)
    try {
      const res = await fetch(`${API_BASE}/api/billing/invoices`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error()
      const data = await res.json()
      window.open(data.portal_url, '_blank')
    } catch {
      setActionError('Kon factuurportaal niet openen')
    } finally {
      setLoadingInvoices(false)
    }
  }

  async function handleCancel() {
    setCancelling(true)
    setActionError(null)
    try {
      const res = await fetch(`${API_BASE}/api/billing/cancel`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error()
      onCancel({ ...status, billing_status: 'cancelled' })
    } catch {
      setActionError('Kon abonnement niet opzeggen')
      setCancelConfirm(false)
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Huidig abonnement</CardTitle>
            <Badge variant="success">Actief</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-[var(--color-muted-foreground)]">Plan</p>
              <p className="font-medium">{PLAN_LABELS[status.plan]}</p>
            </div>
            <div>
              <p className="text-[var(--color-muted-foreground)]">Cyclus</p>
              <p className="font-medium">{CYCLE_LABELS[status.billing_cycle]}</p>
            </div>
            <div>
              <p className="text-[var(--color-muted-foreground)]">Gebruikers</p>
              <p className="font-medium">{status.seats}</p>
            </div>
          </div>
          <div className="pt-3 border-t border-[var(--color-border)]">
            <p className="text-xs text-[var(--color-muted-foreground)]">Totaal excl. BTW</p>
            <p className="text-2xl font-bold text-[var(--color-purple-deep)]">
              {totalPrice(status.plan, status.billing_cycle, status.seats)}
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Facturen</CardTitle>
          <CardDescription>Bekijk en download facturen via het Moneybird-portaal.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="outline"
            onClick={openInvoicePortal}
            disabled={loadingInvoices}
            className="gap-2"
          >
            <ExternalLink size={16} />
            {loadingInvoices ? 'Laden\u2026' : 'Factuurportaal openen'}
          </Button>
        </CardContent>
      </Card>

      {actionError && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle size={16} className="shrink-0" />
          {actionError}
        </div>
      )}

      <div className="border-t border-[var(--color-border)] pt-4">
        {!cancelConfirm ? (
          <button
            type="button"
            onClick={() => setCancelConfirm(true)}
            className="text-sm text-[var(--color-muted-foreground)] hover:text-red-600 transition-colors"
          >
            Abonnement opzeggen
          </button>
        ) : (
          <div className="flex items-center gap-3">
            <p className="text-sm">Weet je het zeker?</p>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleCancel}
              disabled={cancelling}
            >
              {cancelling ? 'Bezig\u2026' : 'Ja, opzeggen'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setCancelConfirm(false)}>
              Annuleren
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

// --- State: payment_failed ---

function PaymentFailedView({ onRetry }: { onRetry: () => void }) {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-red-50 p-4">
          <AlertCircle size={24} className="text-red-600" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-purple-deep)]">Betaling mislukt</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            De SEPA-incasso is afgewezen. Controleer of je bankrekening voldoende saldo heeft en
            probeer opnieuw.
          </p>
        </div>
        <Badge variant="destructive">Betaling mislukt</Badge>
        <Button onClick={onRetry} className="gap-2">
          <CreditCard size={16} />
          Opnieuw proberen
        </Button>
      </CardContent>
    </Card>
  )
}

// --- State: cancelled ---

function CancelledView({ onReactivate }: { onReactivate: () => void }) {
  return (
    <Card>
      <CardContent className="py-12 flex flex-col items-center text-center gap-4">
        <div className="rounded-full bg-[var(--color-sand-light)] p-4">
          <XCircle size={24} className="text-[var(--color-muted-foreground)]" strokeWidth={1.5} />
        </div>
        <div className="space-y-2">
          <p className="font-semibold text-[var(--color-purple-deep)]">Abonnement opgezegd</p>
          <p className="text-sm text-[var(--color-muted-foreground)] max-w-sm">
            Je abonnement is be&euml;indigd. Je kunt het op elk moment heractiveren.
          </p>
        </div>
        <Badge variant="secondary">Opgezegd</Badge>
        <Button variant="outline" onClick={onReactivate} className="gap-2">
          <CheckCircle size={16} />
          Abonnement heractiveren
        </Button>
      </CardContent>
    </Card>
  )
}
