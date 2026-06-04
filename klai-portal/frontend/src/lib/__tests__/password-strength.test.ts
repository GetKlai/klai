import { describe, expect, it } from 'vitest'

import { evaluateSignupPassword, hasSignupPasswordSymbol } from '../password-strength'

describe('signup password strength', () => {
  it('requires the current Zitadel-compatible symbol rule', () => {
    expect(hasSignupPasswordSymbol('correct horse battery staple')).toBe(false)
    expect(hasSignupPasswordSymbol('correct horse battery staple!')).toBe(true)
  })

  it('accepts a strong passphrase with a symbol', async () => {
    const result = await evaluateSignupPassword('correct horse battery staple!', [
      'mark@example.com',
      'Mark',
      'Example BV',
    ])

    expect(result.isAcceptable).toBe(true)
    expect(result.issues).toEqual([])
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
