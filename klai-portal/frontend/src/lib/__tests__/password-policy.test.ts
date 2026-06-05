import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  loadSignupPasswordPolicy,
  resetSignupPasswordPolicyCacheForTests,
} from '../password-policy'

function policyResponse(minLength: number) {
  return new Response(
    JSON.stringify({
      min_length: minLength,
      min_score: 3,
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
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(policyResponse(15))

    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({
      min_length: 15,
    })
    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({
      min_length: 15,
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('shares the first in-flight policy request', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(policyResponse(15))

    const first = loadSignupPasswordPolicy()
    const second = loadSignupPasswordPolicy()

    await expect(Promise.all([first, second])).resolves.toEqual([
      { min_length: 15, min_score: 3 },
      { min_length: 15, min_score: 3 },
    ])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('can force a fresh policy request before submit', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(policyResponse(15))
      .mockResolvedValueOnce(policyResponse(18))

    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({
      min_length: 15,
    })
    await expect(
      loadSignupPasswordPolicy({ force: true }),
    ).resolves.toMatchObject({ min_length: 18 })

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not cache failed policy requests', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(policyResponse(15))

    await expect(loadSignupPasswordPolicy()).rejects.toThrow(
      'Password policy request failed: 503',
    )
    await expect(loadSignupPasswordPolicy()).resolves.toMatchObject({
      min_length: 15,
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
