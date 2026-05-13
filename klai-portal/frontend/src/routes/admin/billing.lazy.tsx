import { createLazyFileRoute } from '@tanstack/react-router'
import { useEffect, useReducer } from 'react'
import { AlertCircle } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { BillingStatusResponse } from './-_billing-types'
import { BillingActiveSection } from './_components/-BillingActiveSection'
import { BillingMandateSection } from './_components/-BillingMandateSection'
import {
  BillingCancelledCard,
  BillingFreeCard,
  BillingMandateRequestedCard,
  BillingPaymentFailedCard,
} from './_components/-BillingStatusCards'

export const Route = createLazyFileRoute('/admin/billing')({
  component: BillingPage,
})

interface BillingPageState {
  billingStatus: BillingStatusResponse | null
  loadingStatus: boolean
  fetchError: string | null
}

type BillingPageAction =
  | { type: 'loaded'; status: BillingStatusResponse }
  | { type: 'load_failed' }
  | { type: 'set_status'; status: BillingStatusResponse }

const initialBillingPageState: BillingPageState = {
  billingStatus: null,
  loadingStatus: true,
  fetchError: null,
}

function billingPageReducer(state: BillingPageState, action: BillingPageAction): BillingPageState {
  switch (action.type) {
    case 'loaded':
      return { billingStatus: action.status, loadingStatus: false, fetchError: null }
    case 'load_failed':
      return { ...state, loadingStatus: false, fetchError: m.admin_billing_error_fetch() }
    case 'set_status':
      return { ...state, billingStatus: action.status }
  }
}

function BillingPage() {
  const auth = useAuth()
  const [state, dispatch] = useReducer(billingPageReducer, initialBillingPageState)

  useEffect(() => {
    if (!auth.isAuthenticated) return
    apiFetch<BillingStatusResponse>(`/api/billing/status`)
      .then((status) => dispatch({ type: 'loaded', status }))
      .catch(() => dispatch({ type: 'load_failed' }))
  }, [auth.isAuthenticated])

  if (state.loadingStatus) {
    return (
      <div className="p-6">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  const billingStatus = state.billingStatus

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6" data-help-id="admin-billing-overview">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">{m.admin_billing_heading()}</h1>
        <p className="text-sm text-gray-400">{m.admin_billing_subtitle()}</p>
      </div>

      {state.fetchError && (
        <div className="flex items-center gap-2 rounded-lg bg-[var(--color-destructive-bg)] px-4 py-3 text-sm text-[var(--color-destructive-text)]">
          <AlertCircle size={16} className="shrink-0" />
          {state.fetchError}
        </div>
      )}

      {billingStatus && (
        <>
          {billingStatus.plan === 'free' && <BillingFreeCard />}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'pending' && (
            <BillingMandateSection onComplete={(status) => dispatch({ type: 'set_status', status })} />
          )}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'mandate_requested' && (
            <BillingMandateRequestedCard />
          )}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'active' && (
            <BillingActiveSection
              status={billingStatus}
              onCancel={(status) => dispatch({ type: 'set_status', status })}
            />
          )}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'payment_failed' && (
            <BillingPaymentFailedCard
              onRetry={() =>
                dispatch({
                  type: 'set_status',
                  status: { ...billingStatus, billing_status: 'pending' },
                })
              }
            />
          )}
          {billingStatus.plan !== 'free' && billingStatus.billing_status === 'cancelled' && (
            <BillingCancelledCard
              onReactivate={() =>
                dispatch({
                  type: 'set_status',
                  status: { ...billingStatus, billing_status: 'pending' },
                })
              }
            />
          )}
        </>
      )}
    </div>
  )
}
