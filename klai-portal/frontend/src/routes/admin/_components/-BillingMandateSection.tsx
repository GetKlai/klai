import { useReducer } from 'react'
import type { FormEvent } from 'react'
import { AlertCircle, CreditCard } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { BillingCycle, BillingStatusResponse, MandateForm } from '../-_billing-types'
import { PLANS } from '../-_billing-types'
import { getCycleLabel, getPlanDescription, planPrice, totalPrice } from '../-_billing-helpers'
import { BillingField } from './-BillingField'

interface BillingMandateSectionProps {
  onComplete: (status: BillingStatusResponse) => void
}

interface BillingMandateState {
  form: MandateForm
  loading: boolean
  error: string | null
}

type BillingMandateAction =
  | { type: 'set_field'; key: keyof MandateForm; value: MandateForm[keyof MandateForm] }
  | { type: 'submit_started' }
  | { type: 'submit_failed'; error: string }
  | { type: 'submit_finished' }

const initialBillingMandateState: BillingMandateState = {
  form: {
    plan: 'knowledge',
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
  },
  loading: false,
  error: null,
}

function billingMandateReducer(
  state: BillingMandateState,
  action: BillingMandateAction,
): BillingMandateState {
  switch (action.type) {
    case 'set_field':
      return { ...state, form: { ...state.form, [action.key]: action.value } }
    case 'submit_started':
      return { ...state, loading: true, error: null }
    case 'submit_failed':
      return { ...state, error: action.error }
    case 'submit_finished':
      return { ...state, loading: false }
  }
}

export function BillingMandateSection({ onComplete }: BillingMandateSectionProps) {
  const [state, dispatch] = useReducer(billingMandateReducer, initialBillingMandateState)
  const form = state.form

  const set = <K extends keyof MandateForm>(key: K, value: MandateForm[K]) =>
    dispatch({ type: 'set_field', key, value })

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    dispatch({ type: 'submit_started' })

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

      const data = await apiFetch<{ mandate_url?: string }>(`/api/billing/mandate`, {
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
    } catch (error) {
      dispatch({
        type: 'submit_failed',
        error: error instanceof Error ? error.message : m.admin_billing_error_connection(),
      })
    } finally {
      dispatch({ type: 'submit_finished' })
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{m.admin_billing_setup_plan_title()}</CardTitle>
          <CardDescription>{m.admin_billing_setup_plan_description()}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
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
                    ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/10 text-gray-900'
                    : 'border-gray-200 text-gray-400 hover:border-[var(--color-rl-accent-dark)]',
                ].join(' ')}
              >
                {getCycleLabel(cycle)}
                {cycle === 'yearly' && (
                  <span className="ml-2 text-xs text-[var(--color-rl-accent)]">{m.admin_billing_yearly_discount()}</span>
                )}
              </button>
            ))}
          </div>

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
                    : 'border-gray-200 hover:border-[var(--color-rl-accent-dark)]',
                ].join(' ')}
              >
                <span className="text-sm font-semibold text-gray-900">{plan.name}</span>
                <span className="mt-0.5 text-xs text-gray-400">{getPlanDescription(plan.id)}</span>
                <span className="mt-3 text-xl font-semibold text-gray-900">
                  &euro;{form.billing_cycle === 'yearly' ? plan.yearly : plan.monthly}
                  <span className="text-xs font-normal text-gray-400">
                    {' '}
                    {m.admin_billing_per_user_month()}
                  </span>
                </span>
              </button>
            ))}
          </div>

          <div className="flex items-end gap-4 pt-1 border-t border-gray-200">
            <div className="space-y-1">
              <Label htmlFor="seats">{m.admin_billing_seats_label()}</Label>
              <Input
                id="seats"
                type="number"
                min={1}
                max={500}
                value={form.seats}
                onChange={(event) => set('seats', Math.max(1, parseInt(event.target.value) || 1))}
                className="w-24"
              />
            </div>
            <div className="ml-auto text-right">
              <p className="text-xs text-gray-400">{m.admin_billing_total_excl_vat()}</p>
              <p className="text-xl font-semibold text-gray-900">
                {totalPrice(form.plan, form.billing_cycle, form.seats)}
              </p>
              {form.billing_cycle === 'yearly' && (
                <p className="text-xs text-gray-400">
                  &euro;{planPrice(form.plan, form.billing_cycle) * form.seats}{' '}
                  {m.admin_billing_monthly_equivalent()}
                </p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_billing_details_title()}</CardTitle>
          <CardDescription>{m.admin_billing_details_description()}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <BillingField
            label={m.admin_billing_field_address()}
            name="address"
            value={form.address}
            onChange={(value) => set('address', value)}
            required
            placeholder={m.admin_billing_placeholder_street()}
          />
          <div className="grid grid-cols-2 gap-3">
            <BillingField
              label={m.admin_billing_field_zipcode()}
              name="zipcode"
              value={form.zipcode}
              onChange={(value) => set('zipcode', value)}
              required
              placeholder={m.admin_billing_placeholder_zipcode()}
            />
            <BillingField
              label={m.admin_billing_field_city()}
              name="city"
              value={form.city}
              onChange={(value) => set('city', value)}
              required
              placeholder={m.admin_billing_placeholder_city()}
            />
          </div>
          <BillingField
            label={m.admin_billing_field_country()}
            name="country"
            value={form.country}
            onChange={(value) => set('country', value)}
            required
          />
          <BillingField
            label={m.admin_billing_field_tax_number()}
            name="tax_number"
            value={form.tax_number}
            onChange={(value) => set('tax_number', value)}
            placeholder={m.admin_billing_placeholder_tax_number()}
          />
          <BillingField
            label={m.admin_billing_field_coc()}
            name="chamber_of_commerce"
            value={form.chamber_of_commerce}
            onChange={(value) => set('chamber_of_commerce', value)}
          />
          <BillingField
            label={m.admin_billing_field_billing_email()}
            name="billing_email"
            type="email"
            value={form.billing_email}
            onChange={(value) => set('billing_email', value)}
            hint={m.admin_billing_field_billing_email_hint()}
          />
          <BillingField
            label={m.admin_billing_field_internal_ref()}
            name="internal_reference"
            value={form.internal_reference}
            onChange={(value) => set('internal_reference', value)}
            hint={m.admin_billing_field_internal_ref_hint()}
          />
        </CardContent>
      </Card>

      {state.error && (
        <div className="flex items-center gap-2 rounded-lg bg-[var(--color-destructive-bg)] px-4 py-3 text-sm text-[var(--color-destructive-text)]">
          <AlertCircle size={16} className="shrink-0" />
          {state.error}
        </div>
      )}

      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-gray-400">{m.admin_billing_sepa_note()}</p>
        <Button type="submit" disabled={state.loading} className="shrink-0 gap-2">
          <CreditCard size={16} />
          {state.loading ? m.admin_billing_submit_loading() : m.admin_billing_submit()}
        </Button>
      </div>
    </form>
  )
}
