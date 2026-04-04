export type Plan = 'core' | 'professional' | 'complete' | 'free'
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

export const PLANS: { id: Plan; name: string; monthly: number; yearly: number }[] = [
  { id: 'core', name: 'Chat', monthly: 22, yearly: 18 },
  { id: 'professional', name: 'Chat + Focus', monthly: 42, yearly: 34 },
  { id: 'complete', name: 'Chat + Focus + Scribe', monthly: 60, yearly: 48 },
]
