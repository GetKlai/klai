export interface OrgGroup {
  id: number
  name: string
}

export interface OrgUser {
  zitadel_user_id: string
  display_name: string
  email: string
}

export interface MemberGroup {
  id: number
  name: string
  role: string
}

export interface MemberUser {
  id: string
  name: string
  email: string
  role: string
}

export interface WizardData {
  name: string
  slug: string
  slugManuallyEdited: boolean
  description: string
  ownerType: 'org' | 'user'
  visibilityMode: 'public' | 'org' | 'restricted'
  allowContribute: boolean
  initialGroups: MemberGroup[]
  initialUsers: MemberUser[]
}

export type Step = 1 | 2 | 3 | 4
