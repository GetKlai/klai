/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §3/§4.4 — the Kennis nav item must reach the
 * knowledge-side screens: knowledge bases, the conversation review queue and
 * knowledge gaps. Gesprekken needs the kb.activity capability AND the tenant
 * unlocks 'widgets' + 'knowledge_activity'; Kennisgaten needs kb.gaps.
 */
import { describe, expect, it } from 'vitest'
import { getAppNavItems, type AppNavAccess } from '../-app-tools'

function access(
  capabilities: string[],
  unlockedFeatures: string[],
  activityQueueCount = 3,
): AppNavAccess {
  return {
    hasCapability: (cap) => capabilities.includes(cap),
    unlockedFeatures,
    activityQueueCount,
  }
}

function knowledgeChildren(
  capabilities: string[],
  unlockedFeatures: string[],
): string[] | undefined {
  const knowledge = getAppNavItems(['knowledge'], access(capabilities, unlockedFeatures)).find(
    (item) => item.to === '/app/knowledge',
  )
  return knowledge?.children?.flatMap((child) => (child.to === undefined ? [] : [child.to]))
}

describe('app nav — Kennis children (SPEC-KNOWLEDGE-ACTIVITY-001 §3)', () => {
  it('lists the three knowledge children and badges Gesprekken with the queue count', () => {
    const nav = getAppNavItems(
      ['knowledge'],
      access(['kb.activity', 'kb.gaps'], ['widgets', 'knowledge_activity']),
    )
    const knowledge = nav.find((item) => item.to === '/app/knowledge')

    expect(knowledge?.children?.map((child) => child.to)).toEqual([
      '/app/knowledge',
      '/app/knowledge/activity',
      '/app/gaps',
    ])
    // Kennisbanken must not stay active for its own children's paths.
    expect(knowledge?.children?.[0]?.end).toBe(true)
    expect(knowledge?.children?.find((child) => child.to === '/app/knowledge/activity')?.badgeCount).toBe(3)
  })

  it('hides Gesprekken without the knowledge_activity tenant unlock', () => {
    expect(
      knowledgeChildren(['kb.activity', 'kb.gaps'], ['widgets']),
    ).toEqual(['/app/knowledge', '/app/gaps'])
  })

  it('hides Kennisgaten without the kb.gaps capability', () => {
    expect(
      knowledgeChildren(['kb.activity'], ['widgets', 'knowledge_activity']),
    ).toEqual(['/app/knowledge', '/app/knowledge/activity'])
  })
})
