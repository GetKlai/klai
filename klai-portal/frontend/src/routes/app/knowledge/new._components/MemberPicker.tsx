import { useRef, useState } from 'react'
import { X, Search } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import type { OrgGroup, OrgUser, MemberGroup, MemberUser } from '../new._types'

function RoleSelect({
  value,
  onChange,
  minRole,
}: {
  value: string
  onChange: (role: string) => void
  minRole: string
}) {
  return (
    <Select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-auto px-2 py-1 text-xs"
    >
      {minRole === 'viewer' && (
        <option value="viewer">{m.knowledge_members_role_viewer()}</option>
      )}
      <option value="contributor">{m.knowledge_members_role_contributor()}</option>
      <option value="owner">{m.knowledge_members_role_owner()}</option>
    </Select>
  )
}

export function MemberPicker({
  initialGroups,
  setInitialGroups,
  initialUsers,
  setInitialUsers,
  availableGroups,
  availableUsers,
  minRole,
  isRestrictedEmpty,
}: {
  initialGroups: MemberGroup[]
  setInitialGroups: (fn: MemberGroup[] | ((prev: MemberGroup[]) => MemberGroup[])) => void
  initialUsers: MemberUser[]
  setInitialUsers: (fn: MemberUser[] | ((prev: MemberUser[]) => MemberUser[])) => void
  availableGroups: OrgGroup[]
  availableUsers: OrgUser[]
  minRole: string
  isRestrictedEmpty: boolean
}) {
  const [groupSearch, setGroupSearch] = useState('')
  const [userSearch, setUserSearch] = useState('')
  const [groupFocused, setGroupFocused] = useState(false)
  const [userFocused, setUserFocused] = useState(false)
  const groupRef = useRef<HTMLDivElement>(null)
  const userRef = useRef<HTMLDivElement>(null)

  const defaultRole = minRole === 'contributor' ? 'contributor' : 'viewer'

  const filteredGroups = availableGroups.filter(
    (g) =>
      g.name.toLowerCase().includes(groupSearch.toLowerCase()) &&
      !initialGroups.some((ig) => ig.id === g.id)
  )

  const filteredUsers = availableUsers.filter(
    (u) =>
      (u.display_name.toLowerCase().includes(userSearch.toLowerCase()) ||
        u.email.toLowerCase().includes(userSearch.toLowerCase())) &&
      !initialUsers.some((iu) => iu.id === u.zitadel_user_id)
  )

  return (
    <div className="flex flex-col gap-4">
      {/* Groups */}
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-[var(--color-foreground)]">
          {m.knowledge_sharing_groups()}
        </span>
        <div
          className="relative"
          ref={groupRef}
          onFocusCapture={() => setGroupFocused(true)}
          onBlurCapture={(e) => {
            if (!groupRef.current?.contains(e.relatedTarget as Node)) {
              setGroupFocused(false)
            }
          }}
        >
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
          <Input
            value={groupSearch}
            onChange={(e) => setGroupSearch(e.target.value)}
            placeholder={m.knowledge_sharing_search_group()}
            className="pl-9"
          />
          {groupFocused && filteredGroups.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md max-h-40 overflow-y-auto">
              {filteredGroups.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    setInitialGroups((prev) => [
                      ...prev,
                      { id: g.id, name: g.name, role: defaultRole },
                    ])
                    setGroupSearch('')
                  }}
                  className="w-full px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-secondary)] transition-colors"
                >
                  {g.name}
                </button>
              ))}
            </div>
          )}
        </div>
        {initialGroups.map((g) => (
          <div
            key={g.id}
            className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2"
          >
            <span className="text-sm text-[var(--color-foreground)]">{g.name}</span>
            <div className="flex items-center gap-2">
              <RoleSelect
                value={g.role}
                onChange={(role) =>
                  setInitialGroups((prev) =>
                    prev.map((ig) => (ig.id === g.id ? { ...ig, role } : ig))
                  )
                }
                minRole={minRole}
              />
              <button
                type="button"
                onClick={() => setInitialGroups((prev) => prev.filter((ig) => ig.id !== g.id))}
                className="flex h-6 w-6 items-center justify-center text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ))}

      </div>

      {/* Persons */}
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-[var(--color-foreground)]">
          {m.knowledge_sharing_persons()}
        </span>
        <div
          className="relative"
          ref={userRef}
          onFocusCapture={() => setUserFocused(true)}
          onBlurCapture={(e) => {
            if (!userRef.current?.contains(e.relatedTarget as Node)) {
              setUserFocused(false)
            }
          }}
        >
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
          <Input
            value={userSearch}
            onChange={(e) => setUserSearch(e.target.value)}
            placeholder={m.knowledge_sharing_search_person()}
            className="pl-9"
          />
          {userFocused && filteredUsers.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md max-h-40 overflow-y-auto">
              {filteredUsers.map((u) => (
                <button
                  key={u.zitadel_user_id}
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    setInitialUsers((prev) => [
                      ...prev,
                      {
                        id: u.zitadel_user_id,
                        name: u.display_name,
                        email: u.email,
                        role: defaultRole,
                      },
                    ])
                    setUserSearch('')
                  }}
                  className="w-full px-3 py-2 text-left text-sm hover:bg-[var(--color-secondary)] transition-colors"
                >
                  <span className="text-[var(--color-foreground)]">{u.display_name}</span>
                  <span className="ml-2 text-xs text-[var(--color-muted-foreground)]">
                    {u.email}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
        {initialUsers.map((u) => (
          <div
            key={u.id}
            className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2"
          >
            <div>
              <span className="text-sm text-[var(--color-foreground)]">{u.name}</span>
              <span className="ml-2 text-xs text-[var(--color-muted-foreground)]">{u.email}</span>
            </div>
            <div className="flex items-center gap-2">
              <RoleSelect
                value={u.role}
                onChange={(role) =>
                  setInitialUsers((prev) =>
                    prev.map((iu) => (iu.id === u.id ? { ...iu, role } : iu))
                  )
                }
                minRole={minRole}
              />
              <button
                type="button"
                onClick={() => setInitialUsers((prev) => prev.filter((iu) => iu.id !== u.id))}
                className="flex h-6 w-6 items-center justify-center text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ))}

        {isRestrictedEmpty && (
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.knowledge_wizard_min_one_member()}
          </p>
        )}
      </div>
    </div>
  )
}
