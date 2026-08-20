import type { Dispatch, SetStateAction } from 'react'

export interface OrgGroup {
  id: number
  name: string
}

export interface OrgUser {
  zitadel_user_id: string
  display_name: string
  email: string
}

export type MemberRole = 'viewer' | 'contributor' | 'owner'
export type OwnerType = 'org' | 'user'
export type VisibilityMode = 'public' | 'org' | 'restricted'

export interface MemberGroup {
  id: number
  name: string
  role: MemberRole
}

export interface MemberUser {
  id: string
  name: string
  email: string
  role: MemberRole
}

export interface WizardData {
  name: string
  slug: string
  slugManuallyEdited: boolean
  description: string
  ownerType: OwnerType
  visibilityMode: VisibilityMode
  allowContribute: boolean
  initialGroups: MemberGroup[]
  initialUsers: MemberUser[]
}

export type Step = 1 | 2 | 3 | 4
/**
 * `org_not_allowed` and `personal_quota` are permanent denials — the wizard
 * must not tell the user to retry. `generic` stays the retryable fallback.
 */
export type WizardErrorKey =
  | 'conflict'
  | 'org_not_allowed'
  | 'personal_quota'
  | 'generic'
  | null
export type WizardDataSetter = Dispatch<SetStateAction<WizardData>>
