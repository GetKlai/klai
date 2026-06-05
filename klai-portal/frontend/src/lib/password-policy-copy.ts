import * as m from '@/paraglide/messages'
import type { SignupPasswordPolicy } from '@/lib/password-policy'
import type { SignupPasswordIssue } from '@/lib/password-strength'

export function passwordPolicyIssueMessage(issue: SignupPasswordIssue | undefined, policy: SignupPasswordPolicy) {
  if (issue === 'too_short') return m.signup_password_too_short({ minLength: String(policy.min_length) })
  return m.signup_password_too_weak()
}

export function passwordStrengthLabel(score: number) {
  const labels = [
    m.signup_password_strength_very_weak(),
    m.signup_password_strength_weak(),
    m.signup_password_strength_fair(),
    m.signup_password_strength_good(),
    m.signup_password_strength_strong(),
  ]
  return labels[Math.max(0, Math.min(4, score))]
}
