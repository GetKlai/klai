import { describe, expect, it } from 'vitest'
import { validateVerifySearch } from '../verify'

describe('/verify route', () => {
  it('accepts Zitadel urlTemplate placeholder names', () => {
    expect(
      validateVerifySearch({
        code: 'CODE123',
        userID: 'user-1',
        orgID: 'org-1',
      }),
    ).toEqual({
      code: 'CODE123',
      userId: 'user-1',
      organization: 'org-1',
    })
  })

  it('keeps legacy verify query names working', () => {
    expect(
      validateVerifySearch({
        code: 'CODE123',
        userId: 'user-1',
        organization: 'org-1',
      }),
    ).toEqual({
      code: 'CODE123',
      userId: 'user-1',
      organization: 'org-1',
    })
  })
})
