import { useState } from 'react'
import { AlertCircle, CreditCard } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { number } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'
import type { BillingCycle, BillingStatusResponse, MandateForm, Plan } from '../_billing-types'
import { PLANS } from '../_billing-types'

// --- Helpers ---

function getPlanDescription(id: Plan): string {
  if (id === 'core') return m.admin_billing_plan_chat_description()
  if (id === 'professional') return m.admin_billing_plan_professional_description()
  return m.admin_billing_plan_complete_description()
}

function getCycleLabel(cycle: BillingCycle): string {
  return cycle === 'monthly' ? m.admin_billing_cycle_monthly() : m.admin_billing_cycle_yearly()
}

function planPrice(plan: Plan, cycle: BillingCycle): number {
  const p = PLANS.find((p) => p.id === plan)!
  return cycle === 'yearly' ? p.yearly : p.monthly
}

function totalPrice(plan: Plan, cycle: BillingCycle, seats: number): string {
  const price = planPrice(plan, cycle) * seats
  return cycle === 'yearly'
    ? `\u20ac${number(getLocale(), price * 12)} ${m.admin_billing_per_year()}`
    : `\u20ac${number(getLocale(), price)} ${m.admin_billing_per_month()}`
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
    <div className="space-y-1.5">
      <Label htmlFor={name}>
        {label}
        {!required && (
          <span className="ml-1 text-xs text-[var(--color-muted-foreground)] font-normal">{m.admin_billing_field_optional()}</span>
        )}
      </Label>
      <Input
        id={name}
        name={name}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        placeholder={placeholder}
      />
      {hint && <p className="text-xs text-[var(--color-muted-foreground)]">{hint}</p>}
    </div>
  )
}

// --- Main component ---

interface BillingSetupViewProps {
  token: string
  onComplete: (s: BillingStatusResponse) => void
}

export function BillingSetupView({ token, onComplete }: BillingSetupViewProps) {
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

      const data = await apiFetch<{ mandate_url?: string }>(`/api/billing/mandate`, token, {
        method: 'POST',
        body: JSON.stringify(body),
      })

      if (data.mandate_url) {
        window.location.href = data.mandate_url
      } else {
        onComplete({
          billing_status: 'mandate_requested',
          plan: form.plan,
          billing_cycle: form.billing_cycle,
          seats: form.seats,
          moneybird_contact_id: null,
        })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : m.admin_billing_error_connection())
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Plan + cycle selection */}
      <Card>
        <CardHeader>
          <CardTitle>{m.admin_billing_setup_plan_title()}</CardTitle>
          <CardDescription>{m.admin_billing_setup_plan_description()}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Cycle toggle */}
          <div className="flex gap-2" role="radiogroup" aria-label={m.admin_billing_active_cycle_label()}>
            {(['monthly', 'yearly'] as BillingCycle[]).map((cycle) => (
              <button
                key={cycle}
                type="button"
                role="radio"
                aria-checked={form.billing_cycle === cycle}
                onClick={() => set('billing_cycle', cycle)}
                className={[
                  'flex-1 rounded-lg border px-4 py-2 text-sm font-medium transition',
                  form.billing_cycle === cycle
                    ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/10 text-[var(--color-foreground)]'
                    : 'border-[var(--color-border)] text-[var(--color-muted-foreground)] hover:border-[var(--color-rl-accent-dark)]',
                ].join(' ')}
              >
                {getCycleLabel(cycle)}
                {cycle === 'yearly' && (
                  <span className="ml-2 text-xs text-[var(--color-rl-accent)]">{m.admin_billing_yearly_discount()}</span>
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
                    ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/10'
                    : 'border-[var(--color-border)] hover:border-[var(--color-rl-accent-dark)]',
                ].join(' ')}
              >
                <span className="text-sm font-semibold text-[var(--color-foreground)]">
                  {plan.name}
                </span>
                <span className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
                  {getPlanDescription(plan.id)}
                </span>
                <span className="mt-3 text-lg font-bold text-[var(--color-foreground)]">
                  &euro;{form.billing_cycle === 'yearly' ? plan.yearly : plan.monthly}
                  <span className="text-xs font-normal text-[var(--color-muted-foreground)]">
                    {' '}
                    {m.admin_billing_per_user_month()}
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
                {m.admin_billing_seats_label()}
              </label>
              <input
                id="seats"
                type="number"
                min={1}
                max={500}
                value={form.seats}
                onChange={(e) => set('seats', Math.max(1, parseInt(e.target.value) || 1))}
                className="w-24 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>
            <div className="ml-auto text-right">
              <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_billing_total_excl_vat()}</p>
              <p className="text-xl font-bold text-[var(--color-foreground)]">
                {totalPrice(form.plan, form.billing_cycle, form.seats)}
              </p>
              {form.billing_cycle === 'yearly' && (
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  &euro;{planPrice(form.plan, form.billing_cycle) * form.seats} {m.admin_billing_monthly_equivalent()}
                </p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Billing details */}
      <Card>
        <CardHeader>
          <CardTitle>{m.admin_billing_details_title()}</CardTitle>
          <CardDescription>
            {m.admin_billing_details_description()}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field
            label={m.admin_billing_field_address()}
            name="address"
            value={form.address}
            onChange={(v) => set('address', v)}
            required
            placeholder="Voorbeeldstraat 1"
          />
          <div className="grid grid-cols-2 gap-3">
            <Field
              label={m.admin_billing_field_zipcode()}
              name="zipcode"
              value={form.zipcode}
              onChange={(v) => set('zipcode', v)}
              required
              placeholder="1234 AB"
            />
            <Field
              label={m.admin_billing_field_city()}
              name="city"
              value={form.city}
              onChange={(v) => set('city', v)}
              required
              placeholder="Amsterdam"
            />
          </div>
          <Field
            label={m.admin_billing_field_country()}
            name="country"
            value={form.country}
            onChange={(v) => set('country', v)}
            required
          />
          <Field
            label={m.admin_billing_field_tax_number()}
            name="tax_number"
            value={form.tax_number}
            onChange={(v) => set('tax_number', v)}
            placeholder="NL123456789B01"
          />
          <Field
            label={m.admin_billing_field_coc()}
            name="chamber_of_commerce"
            value={form.chamber_of_commerce}
            onChange={(v) => set('chamber_of_commerce', v)}
          />
          <Field
            label={m.admin_billing_field_billing_email()}
            name="billing_email"
            type="email"
            value={form.billing_email}
            onChange={(v) => set('billing_email', v)}
            hint={m.admin_billing_field_billing_email_hint()}
          />
          <Field
            label={m.admin_billing_field_internal_ref()}
            name="internal_reference"
            value={form.internal_reference}
            onChange={(v) => set('internal_reference', v)}
            hint={m.admin_billing_field_internal_ref_hint()}
          />
        </CardContent>
      </Card>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-[var(--color-destructive-bg)] px-4 py-3 text-sm text-[var(--color-destructive-text)]">
          <AlertCircle size={16} className="shrink-0" />
          {error}
        </div>
      )}

      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-[var(--color-muted-foreground)]">
          {m.admin_billing_sepa_note()}
        </p>
        <Button type="submit" disabled={loading} className="shrink-0 gap-2">
          <CreditCard size={16} />
          {loading ? m.admin_billing_submit_loading() : m.admin_billing_submit()}
        </Button>
      </div>
    </form>
  )
}
