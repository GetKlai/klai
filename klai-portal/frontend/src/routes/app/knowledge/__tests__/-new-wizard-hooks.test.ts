import { describe, expect, it } from 'vitest'
import { buildCreateKnowledgeBasePayload } from '../new._wizard-hooks'
import type { WizardData } from '../new._types'

function baseWizardData(overrides: Partial<WizardData> = {}): WizardData {
  return {
    name: 'Support KB',
    slug: 'support-kb',
    slugManuallyEdited: false,
    description: '',
    ownerType: 'org',
    visibilityMode: 'org',
    allowContribute: true,
    initialGroups: [],
    initialUsers: [],
    ...overrides,
  }
}

describe('buildCreateKnowledgeBasePayload', () => {
  it('maps org KB settings and initial members to the create API payload', () => {
    const payload = buildCreateKnowledgeBasePayload(
      baseWizardData({
        description: 'Shared support answers',
        visibilityMode: 'restricted',
        allowContribute: false,
        initialGroups: [{ id: 12, name: 'Support', role: 'contributor' }],
        initialUsers: [
          {
            id: 'user-1',
            name: 'Ada Lovelace',
            email: 'ada@example.com',
            role: 'viewer',
          },
        ],
      }),
    )

    expect(payload).toEqual({
      name: 'Support KB',
      slug: 'support-kb',
      description: 'Shared support answers',
      visibility: 'private',
      owner_type: 'org',
      default_org_role: null,
      initial_members: [
        { type: 'group', id: '12', role: 'contributor' },
        { type: 'user', id: 'user-1', role: 'viewer' },
      ],
    })
  })

  it('omits initial members for personal KBs', () => {
    const payload = buildCreateKnowledgeBasePayload(
      baseWizardData({
        ownerType: 'user',
        visibilityMode: 'org',
        initialGroups: [{ id: 12, name: 'Support', role: 'contributor' }],
      }),
    )

    expect(payload).toEqual({
      name: 'Support KB',
      slug: 'support-kb',
      description: undefined,
      visibility: 'internal',
      owner_type: 'user',
      default_org_role: 'contributor',
      initial_members: undefined,
    })
  })
})
