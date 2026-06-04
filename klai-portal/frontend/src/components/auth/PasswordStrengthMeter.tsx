import * as m from '@/paraglide/messages'
import { passwordPolicyIssueMessage, passwordStrengthLabel } from '@/lib/password-policy-copy'
import type { SignupPasswordIssue } from '@/lib/password-strength'

export function PasswordStrengthMeter({
  score,
  issues,
  show,
}: {
  score: number
  issues: SignupPasswordIssue[]
  show: boolean
}) {
  if (!show) return null

  const level = Math.max(0, Math.min(4, score))
  const activeClass =
    level >= 3
      ? 'bg-emerald-500'
      : level === 2
        ? 'bg-amber-500'
        : 'bg-[var(--color-destructive-text)]'

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
          <span className={level >= 3 ? 'text-emerald-700' : level === 2 ? 'text-amber-700' : 'text-gray-500'}>
            {passwordStrengthLabel(level)}
          </span>
        </div>
        <div className="flex min-w-0 items-start justify-between gap-2">
          <span className="shrink-0 text-gray-500">{m.signup_password_policy_label()}</span>
          {issues.length === 0 ? (
            <span className="text-emerald-700">{m.signup_password_policy_met()}</span>
          ) : (
            <span className="text-right text-gray-500">{passwordPolicyIssueMessage(issues[0])}</span>
          )}
        </div>
      </div>
    </div>
  )
}
