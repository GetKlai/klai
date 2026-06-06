import { useEffect, useReducer } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { apiFetch } from '@/lib/apiFetch'
import { adminLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'

type SeatTier = 'chat' | 'knowledge'

interface SeatBreakdownRow {
  seat_type: SeatTier
  count: number
  monthly_eur: number
}

interface SeatBreakdownResponse {
  rows: SeatBreakdownRow[]
  total_users: number
  total_monthly_eur: number
}

interface PerSeatStatusResponse {
  enabled: boolean
  available: boolean
}

interface BillingBreakdownState {
  data: SeatBreakdownResponse | null
  status: PerSeatStatusResponse | null
  error: string | null
  loading: boolean
}

type BillingBreakdownAction =
  | { type: 'breakdown_loaded'; data: SeatBreakdownResponse }
  | { type: 'breakdown_failed'; reason: unknown }
  | { type: 'status_loaded'; status: PerSeatStatusResponse }
  | { type: 'status_failed'; reason: unknown }
  | { type: 'settled' }

const initialBillingBreakdownState: BillingBreakdownState = {
  data: null,
  status: null,
  error: null,
  loading: true,
}

function billingBreakdownReducer(
  state: BillingBreakdownState,
  action: BillingBreakdownAction,
): BillingBreakdownState {
  switch (action.type) {
    case 'breakdown_loaded':
      return { ...state, data: action.data }
    case 'breakdown_failed':
      adminLogger.error('Billing breakdown fetch failed', { reason: action.reason })
      return { ...state, error: m.admin_billing_breakdown_error() }
    case 'status_loaded':
      return { ...state, status: action.status }
    case 'status_failed':
      adminLogger.warn('Per-seat status fetch failed; falling back to disabled', {
        reason: action.reason,
      })
      return { ...state, status: { enabled: false, available: false } }
    case 'settled':
      return { ...state, loading: false }
  }
}

function seatLabel(tier: SeatTier): string {
  if (tier === 'chat') return m.admin_billing_breakdown_account_chat()
  return m.admin_billing_breakdown_account_knowledge()
}

function formatEur(amount: number): string {
  return new Intl.NumberFormat(getLocale(), {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(amount)
}

export function BillingBreakdownSection() {
  const [state, dispatch] = useReducer(billingBreakdownReducer, initialBillingBreakdownState)

  useEffect(() => {
    // Two parallel fetches: the seat counts + the per-tenant per-seat-billing
    // opt-in status. The status fetch has a defined disabled fallback.
    void Promise.allSettled([
      apiFetch<SeatBreakdownResponse>(`/api/admin/billing/breakdown`),
      apiFetch<PerSeatStatusResponse>(`/api/admin/billing/per-seat-status`),
    ])
      .then(([breakdown, perSeat]) => {
        if (breakdown.status === 'fulfilled') {
          dispatch({ type: 'breakdown_loaded', data: breakdown.value })
        } else {
          dispatch({ type: 'breakdown_failed', reason: breakdown.reason })
        }
        if (perSeat.status === 'fulfilled') {
          dispatch({ type: 'status_loaded', status: perSeat.value })
        } else {
          dispatch({ type: 'status_failed', reason: perSeat.reason })
        }
      })
      .finally(() => dispatch({ type: 'settled' }))
  }, [])

  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.admin_billing_breakdown_title()}</CardTitle>
        <CardDescription>{m.admin_billing_breakdown_description()}</CardDescription>
      </CardHeader>
      <CardContent>
        {state.loading && (
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
        )}
        {state.error && (
          <p className="text-sm text-[var(--color-destructive-text)]">{state.error}</p>
        )}
        {state.data && (
          <div className="space-y-2">
            <div className="grid grid-cols-[1fr_auto_auto] gap-x-6 text-xs uppercase tracking-wide text-gray-400">
              <span>{m.admin_billing_breakdown_col_account_type()}</span>
              <span className="text-right">{m.admin_billing_breakdown_col_count()}</span>
              <span className="text-right">{m.admin_billing_breakdown_col_monthly()}</span>
            </div>
            {state.data.rows.map((row) => (
              <div
                key={row.seat_type}
                className="grid grid-cols-[1fr_auto_auto] gap-x-6 text-sm"
              >
                <span className="font-medium">{seatLabel(row.seat_type)}</span>
                <span className="text-right tabular-nums">{row.count}</span>
                <span className="text-right tabular-nums">{formatEur(row.monthly_eur)}</span>
              </div>
            ))}
            <div className="grid grid-cols-[1fr_auto_auto] gap-x-6 border-t border-gray-200 pt-2 text-sm font-semibold">
              <span>{m.admin_billing_breakdown_total()}</span>
              <span className="text-right tabular-nums">{state.data.total_users}</span>
              <span className="text-right tabular-nums">{formatEur(state.data.total_monthly_eur)}</span>
            </div>
          </div>
        )}

        {state.status && !state.status.enabled && (
          <div className="mt-4 rounded-md border border-[var(--color-border)] bg-[var(--color-rl-cream)] px-4 py-3 text-sm">
            <p className="font-medium text-gray-900">{m.admin_billing_per_seat_cta_title()}</p>
            <p className="mt-1 text-xs text-gray-500">
              {m.admin_billing_per_seat_cta_description()}
            </p>
            <Button
              variant="outline"
              size="sm"
              disabled
              className="mt-3 cursor-not-allowed opacity-60"
              title={m.admin_billing_per_seat_cta_unavailable_tooltip()}
            >
              {m.admin_billing_per_seat_cta_button()}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
