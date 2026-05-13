import { Card, CardContent } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import type { OrgGroup, OrgUser, WizardData, WizardDataSetter } from '../new._types'
import { MemberPicker } from './MemberPicker'

export function StepPermissions({
  data,
  setData,
  groups,
  users,
}: {
  data: WizardData
  setData: WizardDataSetter
  groups: OrgGroup[]
  users: OrgUser[]
}) {
  const isRestricted = data.visibilityMode === 'restricted'
  const minRole = !isRestricted && data.allowContribute ? 'contributor' : 'viewer'

  const isRestrictedEmpty =
    isRestricted && data.initialGroups.length === 0 && data.initialUsers.length === 0

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-gray-900">
        {isRestricted
          ? m.knowledge_wizard_title_step3_restricted({ name: data.name })
          : m.knowledge_wizard_title_step3({ name: data.name })}
      </p>

      {!isRestricted && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex flex-col gap-3">
              <p className="text-sm text-gray-900">
                {m.knowledge_wizard_default_role_label()}
              </p>
              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={data.allowContribute}
                  onChange={(e) =>
                    setData((prev) => ({ ...prev, allowContribute: e.target.checked }))
                  }
                  className="mt-1 h-4 w-4 rounded border-gray-200 text-gray-400 focus:ring-[var(--color-ring)]"
                />
                <div>
                  <span className="text-sm font-medium text-gray-900">
                    {m.knowledge_wizard_contributor_checkbox()}
                  </span>
                  <span className="block text-xs text-gray-400">
                    {m.knowledge_sharing_contributor_toggle_description()}
                  </span>
                </div>
              </label>
            </div>
          </CardContent>
        </Card>
      )}

      {isRestricted && (
        <p className="text-sm text-gray-400">
          {m.knowledge_wizard_restricted_desc()}
        </p>
      )}

      {!isRestricted && (
        <div>
          <p className="text-sm font-medium text-gray-900">
            {m.knowledge_wizard_extra_permissions_title()}
          </p>
          <p className="text-xs text-gray-400">
            {m.knowledge_wizard_extra_permissions_desc()}
          </p>
        </div>
      )}

      <MemberPicker
        initialGroups={data.initialGroups}
        setInitialGroups={(fn) =>
          setData((prev) => ({
            ...prev,
            initialGroups: typeof fn === 'function' ? fn(prev.initialGroups) : fn,
          }))
        }
        initialUsers={data.initialUsers}
        setInitialUsers={(fn) =>
          setData((prev) => ({
            ...prev,
            initialUsers: typeof fn === 'function' ? fn(prev.initialUsers) : fn,
          }))
        }
        availableGroups={groups}
        availableUsers={users}
        minRole={minRole}
        isRestrictedEmpty={isRestricted ? isRestrictedEmpty : false}
      />

      <p className="text-xs text-gray-400 italic">
        {m.knowledge_wizard_owner_info()}
      </p>
    </div>
  )
}
