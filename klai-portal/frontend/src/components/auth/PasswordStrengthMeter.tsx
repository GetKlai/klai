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
  const displayLevel = !estimated && issues.includes('too_short') ? 0 : level
  const activeClass =
    displayLevel >= 3
      ? 'bg-emerald-500'
      : displayLevel === 2
        ? 'bg-amber-500'
        : 'bg-[var(--color-destructive-text)]'

  return (
    <div className="space-y-2" aria-live="polite">
      <div className="grid grid-cols-4 gap-1" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <div
            key={index}
            className={`h-1.5 rounded-full ${index <= displayLevel - 1 ? activeClass : 'bg-gray-200'}`}
          />
        ))}
      </div>
      <div className="grid gap-1 text-xs sm:grid-cols-2 sm:gap-3">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <span className="shrink-0 text-gray-500">{m.signup_password_strength_label()}</span>
          <span className={displayLevel >= 3 ? 'text-emerald-700' : displayLevel === 2 ? 'text-amber-700' : 'text-gray-500'}>
            {passwordStrengthLabel(displayLevel)}
          </span>
        </div>
        <div className="flex min-w-0 items-start justify-between gap-2">
          <span className="shrink-0 text-gray-500">{m.signup_password_policy_label()}</span>
          {estimated ? (
            <span className="text-gray-500">{m.signup_password_policy_checking()}</span>
          ) : isAcceptable ? (
            <span className="text-emerald-700">{m.signup_password_policy_met()}</span>
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
