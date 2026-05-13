import { describe, expect, it } from 'vitest'
import { buildCreateKnowledgeBasePayload } from '../new._wizard-hooks'
import type { WizardData } from '../new._types'

const baseWizardData: WizardData = {
  name: 'Support Docs',
  slug: 'support-docs',
  slugManuallyEdited: false,
  description: '',
  ownerType: 'org',
  visibilityMode: 'org',
  allowContribute: true,
  initialGroups: [],
  initialUsers: [],
}

describe('buildCreateKnowledgeBasePayload', () => {
  it('preserves org wizard defaults', () => {
    expect(buildCreateKnowledgeBasePayload(baseWizardData)).toEqual({
      name: 'Support Docs',
      slug: 'support-docs',
      description: undefined,
      visibility: 'internal',
      owner_type: 'org',
      default_org_role: 'contributor',
      initial_members: [],
    })
  })

  it('maps restricted org members into initial_members', () => {
    expect(
      buildCreateKnowledgeBasePayload({
        ...baseWizardData,
        visibilityMode: 'restricted',
        initialGroups: [{ id: 42, name: 'Support', role: 'owner' }],
        initialUsers: [
          {
            id: 'user-1',
            name: 'Ada Lovelace',
            email: 'ada@example.com',
            role: 'viewer',
          },
        ],
      })
    ).toMatchObject({
      visibility: 'private',
      default_org_role: null,
      initial_members: [
        { type: 'group', id: '42', role: 'owner' },
        { type: 'user', id: 'user-1', role: 'viewer' },
      ],
    })
  })

  it('omits initial members for personal knowledge bases', () => {
    expect(
      buildCreateKnowledgeBasePayload({
        ...baseWizardData,
        ownerType: 'user',
        visibilityMode: 'org',
        allowContribute: false,
      })
    ).toMatchObject({
      visibility: 'internal',
      owner_type: 'user',
      default_org_role: 'viewer',
      initial_members: undefined,
    })
  })
})
