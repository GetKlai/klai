import { describe, expect, it } from 'vitest'

import {
  SIGNUP_PASSWORD_MIN_SCORE,
  basicSignupPasswordIssues,
  evaluateSignupPassword,
  hasSignupPasswordSymbol,
} from '../password-strength'

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
    ])

    expect(result.isAcceptable).toBe(true)
    expect(result.issues).toEqual([])
  })

  it('mirrors Zitadel composition requirements before submit', () => {
    expect(basicSignupPasswordIssues('correct horse battery staple 2026!')).toContain('missing_uppercase')
    expect(basicSignupPasswordIssues('Correct horse battery staple!')).toContain('missing_number')
    expect(basicSignupPasswordIssues('Correct horse battery staple 2026')).toContain('missing_symbol')
  })

  it('does not show a passing score while a composition requirement is missing', async () => {
    const result = await evaluateSignupPassword('Correct horse battery staple!', [
      'mark@example.com',
      'Mark',
      'Example BV',
    ])

    expect(result.isAcceptable).toBe(false)
    expect(result.issues).toContain('missing_number')
    expect(result.score).toBeLessThan(SIGNUP_PASSWORD_MIN_SCORE)
  })

  it('rejects passwords based on personal context', async () => {
    const result = await evaluateSignupPassword('Mark!Vletter', [
      'mark@voys.nl',
      'Mark',
      'Vletter',
      'Voys',
    ])

    expect(result.isAcceptable).toBe(false)
    expect(result.issues).toContain('too_predictable')
  })
})
