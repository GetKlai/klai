import * as m from '@/paraglide/messages'
import type { SignupPasswordIssue } from '@/lib/password-strength'

export function passwordPolicyIssueMessage(issue: SignupPasswordIssue | undefined) {
  if (issue === 'too_short') return m.signup_password_too_short()
  if (issue === 'missing_uppercase') return m.signup_password_missing_uppercase()
  if (issue === 'missing_lowercase') return m.signup_password_missing_lowercase()
  if (issue === 'missing_number') return m.signup_password_missing_number()
  if (issue === 'missing_symbol') return m.signup_password_missing_symbol()
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
