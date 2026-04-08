import { useState } from 'react'
import { AlertCircle, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { number } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'
import type { BillingCycle, BillingStatusResponse, Plan } from '../_billing-types'
import { PLANS } from '../_billing-types'

function getPlanLabel(plan: Plan): string {
  if (plan === 'free') return m.admin_billing_free_title()
  const p = PLANS.find((p) => p.id === plan)
  return p ? p.name : plan
}

function getCycleLabel(cycle: BillingCycle): string {
  return cycle === 'monthly' ? m.admin_billing_cycle_monthly() : m.admin_billing_cycle_yearly()
}

function totalPrice(plan: Plan, cycle: BillingCycle, seats: number): string {
  const p = PLANS.find((p) => p.id === plan)!
  const price = (cycle === 'yearly' ? p.yearly : p.monthly) * seats
  return cycle === 'yearly'
    ? `\u20ac${number(getLocale(), price * 12)} ${m.admin_billing_per_year()}`
    : `\u20ac${number(getLocale(), price)} ${m.admin_billing_per_month()}`
}

interface BillingActiveViewProps {
  token: string
  status: BillingStatusResponse
  onCancel: (s: BillingStatusResponse) => void
}

export function BillingActiveView({ token, status, onCancel }: BillingActiveViewProps) {
  const [loadingInvoices, setLoadingInvoices] = useState(false)
  const [cancelConfirm, setCancelConfirm] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function openInvoicePortal() {
    setLoadingInvoices(true)
    setActionError(null)
    try {
      const data = await apiFetch<{ portal_url: string }>(`/api/billing/invoices`, token)
      window.open(data.portal_url, '_blank')
    } catch {
      setActionError(m.admin_billing_error_invoices())
    } finally {
      setLoadingInvoices(false)
    }
  }

  async function handleCancel() {
    setCancelling(true)
    setActionError(null)
    try {
      await apiFetch(`/api/billing/cancel`, token, { method: 'POST' })
      onCancel({ ...status, billing_status: 'cancelled' })
    } catch {
      setActionError(m.admin_billing_error_cancel())
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
            <CardTitle>{m.admin_billing_active_title()}</CardTitle>
            <Badge variant="success">{m.admin_billing_active_badge()}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-[var(--color-muted-foreground)]">{m.admin_billing_active_plan_label()}</p>
              <p className="font-medium">{getPlanLabel(status.plan)}</p>
            </div>
            <div>
              <p className="text-[var(--color-muted-foreground)]">{m.admin_billing_active_cycle_label()}</p>
              <p className="font-medium">{getCycleLabel(status.billing_cycle)}</p>
            </div>
            <div>
              <p className="text-[var(--color-muted-foreground)]">{m.admin_billing_active_seats_label()}</p>
              <p className="font-medium">{status.seats}</p>
            </div>
          </div>
          <div className="pt-3 border-t border-[var(--color-border)]">
            <p className="text-xs text-[var(--color-muted-foreground)]">{m.admin_billing_total_excl_vat()}</p>
            <p className="text-base font-semibold text-[var(--color-foreground)]">
              {totalPrice(status.plan, status.billing_cycle, status.seats)}
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_billing_invoices_title()}</CardTitle>
          <CardDescription>{m.admin_billing_invoices_description()}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="outline"
            onClick={openInvoicePortal}
            disabled={loadingInvoices}
            className="gap-2"
          >
            <ExternalLink size={16} />
            {loadingInvoices ? m.admin_billing_invoices_loading() : m.admin_billing_invoices_button()}
          </Button>
        </CardContent>
      </Card>

      {actionError && (
        <div className="flex items-center gap-2 rounded-lg bg-[var(--color-destructive-bg)] px-4 py-3 text-sm text-[var(--color-destructive-text)]">
          <AlertCircle size={16} className="shrink-0" />
          {actionError}
        </div>
      )}

      <div className="border-t border-[var(--color-border)] pt-4">
        {!cancelConfirm ? (
          <button
            type="button"
            onClick={() => setCancelConfirm(true)}
            className="text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
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
              disabled={cancelling}
            >
              {cancelling ? m.admin_billing_cancel_loading() : m.admin_billing_cancel_confirm_button()}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setCancelConfirm(false)}>
              {m.admin_billing_cancel_abort()}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
