import { beforeEach, describe, expect, it, vi } from 'vitest'

import { loadSignupPasswordPolicy, resetSignupPasswordPolicyCacheForTests } from '../password-policy'

function policyResponse(minLength: number) {
  return new Response(
    JSON.stringify({
      min_length: minLength,
      min_score: 3,
      require_uppercase: true,
      require_lowercase: true,
      require_number: true,
      require_symbol: true,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}

describe('loadSignupPasswordPolicy', () => {
  beforeEach(() => {
    resetSignupPasswordPolicyCacheForTests()
    vi.restoreAllMocks()
  })

  it('caches successful policy requests', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(policyResponse(12))

    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({ min_length: 12 })
    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({ min_length: 12 })

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('can force a fresh policy request before submit', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(policyResponse(12))
      .mockResolvedValueOnce(policyResponse(14))

    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({ min_length: 12 })
    await expect(loadSignupPasswordPolicy({ force: true })).resolves.toMatchObject({ min_length: 14 })

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not cache failed policy requests', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(policyResponse(12))

    await expect(loadSignupPasswordPolicy()).rejects.toThrow('Password policy request failed: 503')
    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({ min_length: 12 })

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
