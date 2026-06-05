import { describe, expect, it } from 'vitest'

import {
  basicSignupPasswordIssues,
  evaluateSignupPassword,
  hasSignupPasswordSymbol,
} from '../password-strength'

const policy = {
  min_length: 12,
  min_score: 3,
  require_uppercase: true,
  require_lowercase: true,
  require_number: true,
  require_symbol: true,
}

describe('signup password strength', () => {
  it('requires the current Zitadel-compatible symbol rule', () => {
    expect(hasSignupPasswordSymbol('correct horse battery staple')).toBe(false)
    expect(hasSignupPasswordSymbol('correct horse battery staple!')).toBe(true)
  })

  it('accepts a strong passphrase with a symbol', async () => {
    const result = await evaluateSignupPassword('Correct horse battery staple 2026!', [
      'mark@example.com',
      'Mark',
      'Example BV',
    ], policy)

    expect(result.isAcceptable).toBe(true)
    expect(result.issues).toEqual([])
  })

  it('mirrors Zitadel composition requirements before submit', () => {
    expect(basicSignupPasswordIssues('correct horse battery staple 2026!', policy)).toContain('missing_uppercase')
    expect(basicSignupPasswordIssues('Correct horse battery staple!', policy)).toContain('missing_number')
    expect(basicSignupPasswordIssues('Correct horse battery staple 2026', policy)).toContain('missing_symbol')
  })

  it('counts Unicode code points for minimum length like the backend', () => {
    expect(
      basicSignupPasswordIssues('Aa1!😀', {
        ...policy,
        min_length: 6,
      }),
    ).toContain('too_short')
  })

  it('keeps strength separate from policy compliance', async () => {
    const result = await evaluateSignupPassword('Correct horse battery staple!', [
      'mark@example.com',
      'Mark',
      'Example BV',
    ], policy)

    expect(result.isAcceptable).toBe(false)
    expect(result.issues).toContain('missing_number')
    expect(result.score).toBeGreaterThanOrEqual(policy.min_score)
  })

  it('rejects passwords based on personal context', async () => {
    const result = await evaluateSignupPassword('Mark!Vletter', [
      'mark@voys.nl',
      'Mark',
      'Vletter',
      'Voys',
    ], policy)

    expect(result.isAcceptable).toBe(false)
    expect(result.issues).toContain('too_predictable')
  })
})
