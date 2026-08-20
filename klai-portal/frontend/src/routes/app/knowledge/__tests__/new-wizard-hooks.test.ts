import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/apiFetch'
import {
  buildCreateKnowledgeBasePayload,
  getCreateKnowledgeBaseErrorKey,
  resolveWizardOwnerScope,
} from '../new._wizard-hooks'
import type { WizardData } from '../new._types'

function baseWizardData(overrides: Partial<WizardData> = {}): WizardData {
  return {
    name: 'Support Docs',
    slug: 'support-docs',
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

/** Mirrors the shape apiFetch builds for `HTTPException(detail={...})`. */
function quotaError(errorCode: string): ApiError {
  return new ApiError(403, JSON.stringify({ error_code: errorCode, plan: 'chat', role: 'admin' }))
}

describe('buildCreateKnowledgeBasePayload', () => {
  it('preserves org wizard defaults', () => {
    expect(buildCreateKnowledgeBasePayload(baseWizardData())).toEqual({
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
    const payload = buildCreateKnowledgeBasePayload(
      baseWizardData({
        description: 'Shared support answers',
        visibilityMode: 'restricted',
        allowContribute: false,
        initialGroups: [{ id: 42, name: 'Support', role: 'owner' }],
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
      name: 'Support Docs',
      slug: 'support-docs',
      description: 'Shared support answers',
      visibility: 'private',
      owner_type: 'org',
      default_org_role: null,
      initial_members: [
        { type: 'group', id: '42', role: 'owner' },
        { type: 'user', id: 'user-1', role: 'viewer' },
      ],
    })
  })

  it('omits initial members for personal knowledge bases', () => {
    const payload = buildCreateKnowledgeBasePayload(
      baseWizardData({
        ownerType: 'user',
        visibilityMode: 'org',
        allowContribute: false,
        initialGroups: [{ id: 42, name: 'Support', role: 'owner' }],
      }),
    )

    expect(payload).toEqual({
      name: 'Support Docs',
      slug: 'support-docs',
      description: undefined,
      visibility: 'internal',
      owner_type: 'user',
      default_org_role: 'viewer',
      initial_members: undefined,
    })
  })
})

describe('resolveWizardOwnerScope', () => {
  it('keeps the org scope when the caller may create org knowledge bases', () => {
    const form = baseWizardData({ ownerType: 'org' })
    expect(resolveWizardOwnerScope(form, true)).toBe(form)
  })

  // Regression: the wizard froze `ownerType: 'org'` into its initial state
  // before /api/me resolved, so a caller who may not create org KBs still
  // POSTed owner_type="org" and got a permanent 403.
  it('falls back to a personal scope when org creation is not allowed', () => {
    const scoped = resolveWizardOwnerScope(baseWizardData({ ownerType: 'org' }), false)

    expect(scoped.ownerType).toBe('user')
    expect(buildCreateKnowledgeBasePayload(scoped).owner_type).toBe('user')
  })

  it('leaves an explicit personal scope untouched', () => {
    const form = baseWizardData({ ownerType: 'user' })
    expect(resolveWizardOwnerScope(form, false)).toBe(form)
  })

  // Regression: the downgrade only rewrote ownerType, so an org-visibility
  // choice made on the access step survived onto a personal KB — a caller who
  // had picked "public" created a publicly visible personal knowledge base.
  it('does not carry an org visibility choice onto a personal KB', () => {
    const scoped = resolveWizardOwnerScope(
      baseWizardData({ ownerType: 'org', visibilityMode: 'public' }),
      false,
    )

    expect(buildCreateKnowledgeBasePayload(scoped).visibility).toBe('internal')
  })

  it('drops org members from the payload after a downgrade', () => {
    const scoped = resolveWizardOwnerScope(
      baseWizardData({
        ownerType: 'org',
        visibilityMode: 'restricted',
        initialGroups: [{ id: 42, name: 'Support', role: 'owner' }],
      }),
      false,
    )

    expect(buildCreateKnowledgeBasePayload(scoped).initial_members).toBeUndefined()
  })
})

describe('getCreateKnowledgeBaseErrorKey', () => {
  it('maps a slug collision to the conflict key', () => {
    expect(getCreateKnowledgeBaseErrorKey(new ApiError(409, 'Slug already exists'))).toBe(
      'conflict',
    )
  })

  // Regression: this 403 is permanent, so the wizard must not render
  // "creation failed, please try again".
  it('maps the org-KB quota denial to a permanent, actionable key', () => {
    expect(getCreateKnowledgeBaseErrorKey(quotaError('kb_quota_org_kb_not_allowed'))).toBe(
      'org_not_allowed',
    )
  })

  it('maps the personal-KB quota denial to its own key', () => {
    expect(getCreateKnowledgeBaseErrorKey(quotaError('kb_quota_personal_kb_exceeded'))).toBe(
      'personal_quota',
    )
  })

  it('falls back to generic for a 403 without a known error code', () => {
    expect(getCreateKnowledgeBaseErrorKey(quotaError('something_else'))).toBe('generic')
    expect(getCreateKnowledgeBaseErrorKey(new ApiError(403, 'Forbidden'))).toBe('generic')
  })

  it('falls back to generic for server and network failures', () => {
    expect(getCreateKnowledgeBaseErrorKey(new ApiError(500, 'boom'))).toBe('generic')
    expect(getCreateKnowledgeBaseErrorKey(new Error('offline'))).toBe('generic')
  })
})
