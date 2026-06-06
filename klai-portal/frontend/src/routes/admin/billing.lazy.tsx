import { createLazyFileRoute } from '@tanstack/react-router'
import * as m from '@/paraglide/messages'
import { BillingBreakdownSection } from './_components/-BillingBreakdownSection'

export const Route = createLazyFileRoute('/admin/billing')({
  component: BillingPage,
})

function BillingPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-6" data-help-id="admin-billing-overview">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">{m.admin_billing_heading()}</h1>
        <p className="text-sm text-gray-400">{m.admin_billing_subtitle()}</p>
      </div>

      <BillingBreakdownSection />
    </div>
  )
}
