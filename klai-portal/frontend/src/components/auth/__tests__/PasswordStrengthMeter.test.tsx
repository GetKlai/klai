import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PasswordStrengthMeter } from '../PasswordStrengthMeter'

vi.mock('@/paraglide/messages', () => ({
  signup_password_missing_lowercase: () => 'Add lowercase.',
  signup_password_missing_number: () => 'Add number.',
  signup_password_missing_symbol: () => 'Add symbol.',
  signup_password_missing_uppercase: () => 'Add uppercase.',
  signup_password_policy_checking: () => 'Checking',
  signup_password_policy_label: () => 'Policy',
  signup_password_policy_met: () => 'Meets policy',
  signup_password_strength_fair: () => 'Could be stronger',
  signup_password_strength_good: () => 'Good',
  signup_password_strength_label: () => 'Strength',
  signup_password_strength_strong: () => 'Strong',
  signup_password_strength_very_weak: () => 'Very weak',
  signup_password_strength_weak: () => 'Weak',
  signup_password_too_short: ({ minLength }: { minLength: string }) => `Use at least ${minLength} characters.`,
  signup_password_too_weak: () => 'Too weak.',
}))

describe('PasswordStrengthMeter', () => {
  it('does not show policy compliance while strength is still estimated', () => {
    render(
      <PasswordStrengthMeter
        score={2}
        issues={[]}
        show
        isAcceptable={false}
        estimated
        policy={{
          min_length: 12,
          min_score: 3,
          require_uppercase: true,
          require_lowercase: true,
          require_number: true,
          require_symbol: true,
        }}
      />,
    )

    expect(screen.getByText('Checking')).toBeTruthy()
    expect(screen.queryByText('Meets policy')).toBeNull()
  })
})
