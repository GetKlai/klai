import { getLocale } from '@/paraglide/runtime'
import { number } from '@/paraglide/registry'
import * as m from '@/paraglide/messages'
import type { BillingCycle, Plan } from './-_billing-types'
import { PLANS } from './-_billing-types'

export function getPlanDescription(id: Plan): string {
  if (id === 'chat') return m.admin_billing_plan_chat_description()
  return m.admin_billing_plan_knowledge_description()
}

export function getPlanLabel(plan: Plan): string {
  if (plan === 'free') return m.admin_billing_free_title()
  const p = PLANS.find((candidate) => candidate.id === plan)
  return p ? p.name : plan
}

export function getCycleLabel(cycle: BillingCycle): string {
  return cycle === 'monthly' ? m.admin_billing_cycle_monthly() : m.admin_billing_cycle_yearly()
}

export function planPrice(plan: Plan, cycle: BillingCycle): number {
  const p = PLANS.find((candidate) => candidate.id === plan)!
  return cycle === 'yearly' ? p.yearly : p.monthly
}

export function totalPrice(plan: Plan, cycle: BillingCycle, seats: number): string {
  const price = planPrice(plan, cycle) * seats
  return cycle === 'yearly'
    ? `\u20ac${number(getLocale(), price * 12)} ${m.admin_billing_per_year()}`
    : `\u20ac${number(getLocale(), price)} ${m.admin_billing_per_month()}`
}

export function formatEur(amount: number): string {
  return new Intl.NumberFormat(getLocale(), {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(amount)
}
