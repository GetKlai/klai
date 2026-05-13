import { useReducer } from 'react'
import { AlertCircle, ExternalLink } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { BillingStatusResponse } from '../-_billing-types'
import { getCycleLabel, getPlanLabel, totalPrice } from '../-_billing-helpers'
import { BillingBreakdownSection } from './-BillingBreakdownSection'

interface BillingActiveSectionProps {
  status: BillingStatusResponse
  onCancel: (status: BillingStatusResponse) => void
}

interface BillingActiveState {
  loadingInvoices: boolean
  cancelConfirm: boolean
  cancelling: boolean
  actionError: string | null
}

type BillingActiveAction =
  | { type: 'invoice_started' }
  | { type: 'invoice_failed' }
  | { type: 'invoice_finished' }
  | { type: 'show_cancel_confirm' }
  | { type: 'hide_cancel_confirm' }
  | { type: 'cancel_started' }
  | { type: 'cancel_failed' }
  | { type: 'cancel_finished' }

const initialBillingActiveState: BillingActiveState = {
  loadingInvoices: false,
  cancelConfirm: false,
  cancelling: false,
  actionError: null,
}

function billingActiveReducer(state: BillingActiveState, action: BillingActiveAction): BillingActiveState {
  switch (action.type) {
    case 'invoice_started':
      return { ...state, loadingInvoices: true, actionError: null }
    case 'invoice_failed':
      return { ...state, actionError: m.admin_billing_error_invoices() }
    case 'invoice_finished':
      return { ...state, loadingInvoices: false }
    case 'show_cancel_confirm':
      return { ...state, cancelConfirm: true }
    case 'hide_cancel_confirm':
      return { ...state, cancelConfirm: false }
    case 'cancel_started':
      return { ...state, cancelling: true, actionError: null }
    case 'cancel_failed':
      return { ...state, actionError: m.admin_billing_error_cancel(), cancelConfirm: false }
    case 'cancel_finished':
      return { ...state, cancelling: false }
  }
}

export function BillingActiveSection({ status, onCancel }: BillingActiveSectionProps) {
  const [state, dispatch] = useReducer(billingActiveReducer, initialBillingActiveState)

  async function openInvoicePortal() {
    dispatch({ type: 'invoice_started' })
    try {
      const data = await apiFetch<{ portal_url: string }>(`/api/billing/invoices`)
      window.open(data.portal_url, '_blank')
    } catch {
      dispatch({ type: 'invoice_failed' })
    } finally {
      dispatch({ type: 'invoice_finished' })
    }
  }

  async function handleCancel() {
    dispatch({ type: 'cancel_started' })
    try {
      await apiFetch(`/api/billing/cancel`, { method: 'POST' })
      onCancel({ ...status, billing_status: 'cancelled' })
    } catch {
      dispatch({ type: 'cancel_failed' })
    } finally {
      dispatch({ type: 'cancel_finished' })
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>{m.admin_billing_active_title()}</CardTitle>
            <Badge variant="success">{m.admin_billing_active_badge()}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-gray-400">{m.admin_billing_active_plan_label()}</p>
              <p className="font-medium">{getPlanLabel(status.plan)}</p>
            </div>
            <div>
              <p className="text-gray-400">{m.admin_billing_active_cycle_label()}</p>
              <p className="font-medium">{getCycleLabel(status.billing_cycle)}</p>
            </div>
            <div>
              <p className="text-gray-400">{m.admin_billing_active_seats_label()}</p>
              <p className="font-medium">{status.seats}</p>
            </div>
          </div>
          <div className="pt-3 border-t border-gray-200">
            <p className="text-xs text-gray-400">{m.admin_billing_total_excl_vat()}</p>
            <p className="text-xl font-semibold text-gray-900">
              {totalPrice(status.plan, status.billing_cycle, status.seats)}
            </p>
          </div>
        </CardContent>
      </Card>

      <BillingBreakdownSection />

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_billing_invoices_title()}</CardTitle>
          <CardDescription>{m.admin_billing_invoices_description()}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="outline"
            onClick={openInvoicePortal}
            disabled={state.loadingInvoices}
            className="gap-2"
          >
            <ExternalLink size={16} />
            {state.loadingInvoices ? m.admin_billing_invoices_loading() : m.admin_billing_invoices_button()}
          </Button>
        </CardContent>
      </Card>

      {state.actionError && (
        <div className="flex items-center gap-2 rounded-lg bg-[var(--color-destructive-bg)] px-4 py-3 text-sm text-[var(--color-destructive-text)]">
          <AlertCircle size={16} className="shrink-0" />
          {state.actionError}
        </div>
      )}

      <div className="border-t border-gray-200 pt-4">
        {!state.cancelConfirm ? (
          <button
            type="button"
            onClick={() => dispatch({ type: 'show_cancel_confirm' })}
            className="text-sm text-gray-400 hover:text-[var(--color-destructive)] transition-colors"
          >
            {m.admin_billing_cancel_link()}
          </button>
        ) : (
          <div className="flex items-center gap-3">
            <p className="text-sm">{m.admin_billing_cancel_confirm()}</p>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleCancel}
              disabled={state.cancelling}
            >
              {state.cancelling ? m.admin_billing_cancel_loading() : m.admin_billing_cancel_confirm_button()}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => dispatch({ type: 'hide_cancel_confirm' })}>
              {m.admin_billing_cancel_abort()}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
