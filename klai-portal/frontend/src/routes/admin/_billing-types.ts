export type Plan = 'chat' | 'knowledge' | 'free'
export type BillingCycle = 'monthly' | 'yearly'
export type BillingStatus = 'pending' | 'mandate_requested' | 'active' | 'payment_failed' | 'cancelled'

export interface BillingStatusResponse {
  billing_status: BillingStatus
  plan: Plan
  billing_cycle: BillingCycle
  seats: number
  moneybird_contact_id: string | null
}

export interface MandateForm {
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

// SPEC-PORTAL-PLAN-RENAME-001: 2-tier plan ladder. Prices match the live
// pricing page on getklai.com/pricing.
export const PLANS: { id: Plan; name: string; monthly: number; yearly: number }[] = [
  { id: 'chat', name: 'Klai Chat', monthly: 28, yearly: 20 },
  { id: 'knowledge', name: 'Klai Chat + Knowledge', monthly: 68, yearly: 48 },
]
