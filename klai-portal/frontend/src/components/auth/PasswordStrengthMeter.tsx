import * as m from '@/paraglide/messages'
import { passwordPolicyIssueMessage, passwordStrengthLabel } from '@/lib/password-policy-copy'
import type { SignupPasswordPolicy } from '@/lib/password-policy'
import type { SignupPasswordIssue } from '@/lib/password-strength'

export function PasswordStrengthMeter({
  score,
  issues,
  show,
  isAcceptable,
  estimated,
  policy,
}: {
  score: number
  issues: SignupPasswordIssue[]
  show: boolean
  isAcceptable: boolean
  estimated: boolean
  policy: SignupPasswordPolicy | null
}) {
  if (!show) return null

  const level = Math.max(0, Math.min(4, score))
  const isTooShort = !estimated && issues.includes('too_short')
  const strengthLabel = isTooShort
    ? m.signup_password_strength_too_short({ strength: passwordStrengthLabel(level) })
    : passwordStrengthLabel(level)
  const activeClass =
    isAcceptable && level >= 3
      ? 'bg-[var(--color-success)]'
      : level >= 2
        ? 'bg-[var(--color-warning)]'
        : 'bg-[var(--color-destructive-text)]'
  const strengthTextClass = isTooShort
    ? 'text-[var(--color-warning-text)]'
    : level >= 3
      ? 'text-[var(--color-success-text)]'
      : level === 2
        ? 'text-[var(--color-warning-text)]'
        : 'text-gray-500'

  return (
    <div className="space-y-2" aria-live="polite">
      <div className="grid grid-cols-4 gap-1" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <div
            key={index}
            className={`h-1.5 rounded-full ${index <= level - 1 ? activeClass : 'bg-gray-200'}`}
          />
        ))}
      </div>
      <div className="grid gap-1 text-xs sm:grid-cols-2 sm:gap-3">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <span className="shrink-0 text-gray-500">{m.signup_password_strength_label()}</span>
          <span className={`text-right ${strengthTextClass}`}>
            {strengthLabel}
          </span>
        </div>
        <div className="flex min-w-0 items-start justify-between gap-2">
          <span className="shrink-0 text-gray-500">{m.signup_password_policy_label()}</span>
          {estimated ? (
            <span className="text-gray-500">{m.signup_password_policy_checking()}</span>
          ) : isAcceptable ? (
            <span className="text-[var(--color-success-text)]">{m.signup_password_policy_met()}</span>
          ) : policy ? (
            <span className="text-right text-gray-500">{passwordPolicyIssueMessage(issues[0], policy)}</span>
          ) : (
            <span className="text-gray-500">{m.signup_password_policy_checking()}</span>
          )}
        </div>
      </div>
    </div>
  )
}
