import { describe, expect, it } from 'vitest'
import { FALLBACK_MAX_PERSONAL_KBS, resolvePersonalKBLimit } from '@/hooks/useKBQuota'

/**
 * The personal-KB cap arrives from `/api/me` as `number | null | undefined`:
 *
 *   number    -> hard cap enforced by assert_can_create_personal_kb
 *   null      -> unlimited
 *   undefined -> older backend, fall back to the most restrictive paid tier
 *
 * `null` and `undefined` must NOT be collapsed. `value ?? FALLBACK` does
 * exactly that and would cap an unlimited caller at the fallback.
 */
describe('resolvePersonalKBLimit', () => {
  it('keeps an explicit null as unlimited', () => {
    expect(resolvePersonalKBLimit({ max_personal_kbs_per_user: null })).toBeNull()
  })

  it('falls back to the restrictive tier when the backend omits the field', () => {
    expect(resolvePersonalKBLimit({})).toBe(FALLBACK_MAX_PERSONAL_KBS)
  })

  it('uses the cap verbatim when the backend sends one', () => {
    expect(resolvePersonalKBLimit({ max_personal_kbs_per_user: 5 })).toBe(5)
    expect(resolvePersonalKBLimit({ max_personal_kbs_per_user: 0 })).toBe(0)
  })

  it('has no cap to enforce while the user is still loading', () => {
    expect(resolvePersonalKBLimit(undefined)).toBeNull()
  })

})

