import { describe, it, expect } from 'vitest'
import { PROFILE_LADDER, PROFILE_RANK, meetsMinRole } from '../profiles'

describe('profiles', () => {
  describe('PROFILE_LADDER', () => {
    it('has 5 rungs in ascending order', () => {
      expect(PROFILE_LADDER).toEqual(['personal', 'company', 'kb_manager', 'group_manager', 'admin'])
    })
  })

  describe('PROFILE_RANK', () => {
    it('assigns strictly ascending ranks', () => {
      const ranks = PROFILE_LADDER.map((r) => PROFILE_RANK[r])
      for (let i = 1; i < ranks.length; i++) {
        expect(ranks[i]).toBeGreaterThan(ranks[i - 1])
      }
    })
  })

  describe('meetsMinRole', () => {
    it('returns false for undefined role', () => {
      expect(meetsMinRole(undefined, 'personal')).toBe(false)
    })

    it('returns false for unknown role string', () => {
      expect(meetsMinRole('old-member', 'personal')).toBe(false)
    })

    it('returns true when role equals minRole', () => {
      expect(meetsMinRole('kb_manager', 'kb_manager')).toBe(true)
    })

    it('returns true when role exceeds minRole', () => {
      expect(meetsMinRole('admin', 'kb_manager')).toBe(true)
      expect(meetsMinRole('group_manager', 'personal')).toBe(true)
    })

    it('returns false when role is below minRole', () => {
      expect(meetsMinRole('personal', 'kb_manager')).toBe(false)
      expect(meetsMinRole('company', 'group_manager')).toBe(false)
    })

    it('covers every combination on the ladder', () => {
      PROFILE_LADDER.forEach((role, roleIdx) => {
        PROFILE_LADDER.forEach((minRole, minIdx) => {
          const result = meetsMinRole(role, minRole)
          expect(result).toBe(roleIdx >= minIdx)
        })
      })
    })
  })
})
