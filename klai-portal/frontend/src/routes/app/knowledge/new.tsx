import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, Globe, Users, Lock, X, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Card, CardContent } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/knowledge/new')({
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgeNewPage />
    </ProductGuard>
  ),
})

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

interface OrgGroup {
  id: number
  name: string
}

interface OrgUser {
  zitadel_user_id: string
  display_name: string
  email: string
}

interface InitialGroup {
  id: number
  name: string
  role: string
}

interface InitialUser {
  id: string
  name: string
  email: string
  role: string
}

function KnowledgeNewPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugManuallyEdited, setSlugManuallyEdited] = useState(false)
  const [ownerType, setOwnerType] = useState<'org' | 'user'>('org')
  const [errorKey, setErrorKey] = useState<'conflict' | 'generic' | null>(null)

  const [visibilityMode, setVisibilityMode] = useState<'public' | 'org' | 'restricted'>('org')
  const [allowContribute, setAllowContribute] = useState(false)
  const [initialGroups, setInitialGroups] = useState<InitialGroup[]>([])
  const [initialUsers, setInitialUsers] = useState<InitialUser[]>([])
  const [groupSearch, setGroupSearch] = useState('')
  const [userSearch, setUserSearch] = useState('')

  const { data: groupsData } = useQuery({
    queryKey: ['app-groups'],
    queryFn: () => apiFetch<{ groups: OrgGroup[] }>('/api/app/groups', token),
    enabled: !!token && ownerType === 'org' && visibilityMode === 'restricted',
  })

  const { data: usersData } = useQuery({
    queryKey: ['app-users'],
    queryFn: () =>
      apiFetch<{ users: OrgUser[] }>('/api/app/users', token),
    enabled: !!token && ownerType === 'org' && visibilityMode === 'restricted',
  })

  const filteredGroups = (groupsData?.groups ?? []).filter(
    (g) =>
      g.name.toLowerCase().includes(groupSearch.toLowerCase()) &&
      !initialGroups.some((ig) => ig.id === g.id)
  )

  const filteredUsers = (usersData?.users ?? []).filter(
    (u) =>
      (u.display_name.toLowerCase().includes(userSearch.toLowerCase()) ||
        u.email.toLowerCase().includes(userSearch.toLowerCase())) &&
      !initialUsers.some((iu) => iu.id === u.zitadel_user_id)
  )

  function handleNameChange(value: string) {
    setName(value)
    if (!slugManuallyEdited) {
      setSlug(slugify(value))
    }
  }

  function handleSlugChange(value: string) {
    setSlugManuallyEdited(true)
    setSlug(slugify(value))
  }

  const isRestrictedEmpty =
    visibilityMode === 'restricted' &&
    initialGroups.length === 0 &&
    initialUsers.length === 0

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const visibility =
        visibilityMode === 'public'
          ? 'public'
          : visibilityMode === 'org'
            ? 'internal'
            : 'private'
      const defaultOrgRole =
        visibilityMode === 'restricted'
          ? null
          : allowContribute
            ? 'contributor'
            : 'viewer'

      return apiFetch<{ slug: string }>(`/api/app/knowledge-bases`, token, {
        method: 'POST',
        body: JSON.stringify({
          name,
          slug,
          visibility,
          owner_type: ownerType,
          default_org_role: defaultOrgRole,
          initial_members:
            visibilityMode === 'restricted'
              ? [
                  ...initialGroups.map((g) => ({
                    type: 'group',
                    id: String(g.id),
                    role: g.role,
                  })),
                  ...initialUsers.map((u) => ({
                    type: 'user',
                    id: u.id,
                    role: u.role,
                  })),
                ]
              : undefined,
        }),
      })
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug: data.slug } })
    },
    onError: (err: Error) => {
      setErrorKey(err instanceof ApiError && err.status === 409 ? 'conflict' : 'generic')
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setErrorKey(null)
    mutate()
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center gap-3 mb-6">
        <Brain className="h-6 w-6 text-[var(--color-purple-deep)]" />
        <h1 className="font-serif text-xl font-bold text-[var(--color-purple-deep)]">
          {m.knowledge_new_heading()}
        </h1>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {/* Scope picker */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-scope">{m.knowledge_new_scope_label()}</Label>
          <div className="grid grid-cols-2 gap-3">
            {(['org', 'user'] as const).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setOwnerType(type)}
                className={[
                  'flex flex-col items-start gap-1 rounded-xl border p-4 text-left transition-all',
                  ownerType === type
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 ring-1 ring-[var(--color-accent)]'
                    : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50',
                ].join(' ')}
              >
                <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                  {type === 'org' ? m.knowledge_new_scope_org() : m.knowledge_new_scope_personal()}
                </span>
                <span className="text-xs text-[var(--color-muted-foreground)]">
                  {type === 'org'
                    ? m.knowledge_new_scope_org_description()
                    : m.knowledge_new_scope_personal_description()}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Name */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-name">{m.knowledge_new_name_label()}</Label>
          <Input
            id="kb-name"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder={m.knowledge_new_name_placeholder()}
            required
          />
        </div>

        {/* Slug */}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="kb-slug">{m.knowledge_new_slug_label()}</Label>
          <Input
            id="kb-slug"
            value={slug}
            onChange={(e) => handleSlugChange(e.target.value)}
            required
            pattern="[a-z0-9\-]+"
          />
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.knowledge_new_slug_hint()}
          </p>
          {errorKey === 'conflict' && (
            <p className="text-xs text-[var(--color-destructive)]">
              {m.knowledge_new_slug_conflict()}
            </p>
          )}
        </div>

        {/* Visibility cards */}
        {ownerType === 'org' && (
          <div className="flex flex-col gap-1.5">
            <Label>{m.knowledge_sharing_who_can_access()}</Label>
            <div className="flex flex-col gap-2">
              {(
                [
                  {
                    key: 'public' as const,
                    icon: Globe,
                    title: m.knowledge_sharing_visibility_public(),
                    desc: m.knowledge_sharing_visibility_public_description(),
                  },
                  {
                    key: 'org' as const,
                    icon: Users,
                    title: m.knowledge_sharing_visibility_org(),
                    desc: m.knowledge_sharing_visibility_org_description(),
                  },
                  {
                    key: 'restricted' as const,
                    icon: Lock,
                    title: m.knowledge_sharing_visibility_restricted(),
                    desc: m.knowledge_sharing_visibility_restricted_description(),
                  },
                ] as const
              ).map(({ key, icon: Icon, title, desc }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => {
                    setVisibilityMode(key)
                    if (key === 'restricted') setAllowContribute(false)
                  }}
                  className={[
                    'flex items-start gap-3 rounded-xl border p-4 text-left transition-all',
                    visibilityMode === key
                      ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 ring-1 ring-[var(--color-accent)]'
                      : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50',
                  ].join(' ')}
                >
                  <Icon className="h-5 w-5 mt-0.5 text-[var(--color-accent)]" />
                  <div>
                    <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                      {title}
                    </span>
                    <span className="block text-xs text-[var(--color-muted-foreground)]">
                      {desc}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Contributor checkbox */}
        {ownerType === 'org' && visibilityMode !== 'restricted' && (
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={allowContribute}
              onChange={(e) => setAllowContribute(e.target.checked)}
              className="mt-1 h-4 w-4 rounded border-[var(--color-border)] text-[var(--color-accent)] focus:ring-[var(--color-ring)]"
            />
            <div>
              <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                {m.knowledge_sharing_contributor_toggle()}
              </span>
              <span className="block text-xs text-[var(--color-muted-foreground)]">
                {m.knowledge_sharing_contributor_toggle_description()}
              </span>
            </div>
          </label>
        )}

        {/* Member picker (restricted mode) */}
        {ownerType === 'org' && visibilityMode === 'restricted' && (
          <MemberPicker
            initialGroups={initialGroups}
            setInitialGroups={setInitialGroups}
            initialUsers={initialUsers}
            setInitialUsers={setInitialUsers}
            filteredGroups={filteredGroups}
            filteredUsers={filteredUsers}
            groupSearch={groupSearch}
            setGroupSearch={setGroupSearch}
            userSearch={userSearch}
            setUserSearch={setUserSearch}
            isRestrictedEmpty={isRestrictedEmpty}
          />
        )}

        {/* Summary card */}
        {name && (
          <SummaryCard
            name={name}
            slug={slug}
            ownerType={ownerType}
            visibilityMode={visibilityMode}
            allowContribute={allowContribute}
            initialGroups={initialGroups}
            initialUsers={initialUsers}
          />
        )}

        {/* Error */}
        {errorKey === 'generic' && (
          <p className="text-sm text-[var(--color-destructive)]">{m.knowledge_new_error()}</p>
        )}

        {/* Actions */}
        <div className="flex gap-3 pt-2">
          <Button type="submit" disabled={isPending || !name || !slug || isRestrictedEmpty}>
            {m.knowledge_new_submit()}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void navigate({ to: '/app/knowledge' })}
          >
            {m.knowledge_new_cancel()}
          </Button>
        </div>
      </form>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RoleSelect({
  value,
  onChange,
}: {
  value: string
  onChange: (role: string) => void
}) {
  return (
    <Select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-auto px-2 py-1 text-xs"
    >
      <option value="viewer">{m.knowledge_members_role_viewer()}</option>
      <option value="contributor">{m.knowledge_members_role_contributor()}</option>
    </Select>
  )
}

function MemberPicker({
  initialGroups,
  setInitialGroups,
  initialUsers,
  setInitialUsers,
  filteredGroups,
  filteredUsers,
  groupSearch,
  setGroupSearch,
  userSearch,
  setUserSearch,
  isRestrictedEmpty,
}: {
  initialGroups: InitialGroup[]
  setInitialGroups: React.Dispatch<React.SetStateAction<InitialGroup[]>>
  initialUsers: InitialUser[]
  setInitialUsers: React.Dispatch<React.SetStateAction<InitialUser[]>>
  filteredGroups: OrgGroup[]
  filteredUsers: OrgUser[]
  groupSearch: string
  setGroupSearch: (v: string) => void
  userSearch: string
  setUserSearch: (v: string) => void
  isRestrictedEmpty: boolean
}) {
  return (
    <div className="flex flex-col gap-4">
      <Label>{m.knowledge_sharing_share_with()}</Label>

      {/* Groups */}
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-[var(--color-purple-deep)]">
          {m.knowledge_sharing_groups()}
        </span>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
          <Input
            value={groupSearch}
            onChange={(e) => setGroupSearch(e.target.value)}
            placeholder={m.knowledge_sharing_search_group()}
            className="pl-9"
          />
          {groupSearch && filteredGroups.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md max-h-40 overflow-y-auto">
              {filteredGroups.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  onClick={() => {
                    setInitialGroups((prev) => [
                      ...prev,
                      { id: g.id, name: g.name, role: 'viewer' },
                    ])
                    setGroupSearch('')
                  }}
                  className="w-full px-3 py-2 text-left text-sm text-[var(--color-purple-deep)] hover:bg-[var(--color-secondary)] transition-colors"
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
            <span className="text-sm text-[var(--color-purple-deep)]">{g.name}</span>
            <div className="flex items-center gap-2">
              <Label className="text-xs text-[var(--color-muted-foreground)]">
                {m.knowledge_sharing_role_label()}
              </Label>
              <RoleSelect
                value={g.role}
                onChange={(role) =>
                  setInitialGroups((prev) =>
                    prev.map((ig) => (ig.id === g.id ? { ...ig, role } : ig))
                  )
                }
              />
              <button
                type="button"
                onClick={() =>
                  setInitialGroups((prev) => prev.filter((ig) => ig.id !== g.id))
                }
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
        <span className="text-sm font-medium text-[var(--color-purple-deep)]">
          {m.knowledge_sharing_persons()}
        </span>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
          <Input
            value={userSearch}
            onChange={(e) => setUserSearch(e.target.value)}
            placeholder={m.knowledge_sharing_search_person()}
            className="pl-9"
          />
          {userSearch && filteredUsers.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md max-h-40 overflow-y-auto">
              {filteredUsers.map((u) => (
                <button
                  key={u.zitadel_user_id}
                  type="button"
                  onClick={() => {
                    setInitialUsers((prev) => [
                      ...prev,
                      {
                        id: u.zitadel_user_id,
                        name: u.display_name,
                        email: u.email,
                        role: 'viewer',
                      },
                    ])
                    setUserSearch('')
                  }}
                  className="w-full px-3 py-2 text-left text-sm hover:bg-[var(--color-secondary)] transition-colors"
                >
                  <span className="text-[var(--color-purple-deep)]">{u.display_name}</span>
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
              <span className="text-sm text-[var(--color-purple-deep)]">{u.name}</span>
              <span className="ml-2 text-xs text-[var(--color-muted-foreground)]">
                {u.email}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Label className="text-xs text-[var(--color-muted-foreground)]">
                {m.knowledge_sharing_role_label()}
              </Label>
              <RoleSelect
                value={u.role}
                onChange={(role) =>
                  setInitialUsers((prev) =>
                    prev.map((iu) => (iu.id === u.id ? { ...iu, role } : iu))
                  )
                }
              />
              <button
                type="button"
                onClick={() =>
                  setInitialUsers((prev) => prev.filter((iu) => iu.id !== u.id))
                }
                className="flex h-6 w-6 items-center justify-center text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ))}

        {isRestrictedEmpty && (
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.knowledge_sharing_min_one_member()}
          </p>
        )}
      </div>
    </div>
  )
}

function SummaryCard({
  name,
  slug,
  ownerType,
  visibilityMode,
  allowContribute,
  initialGroups,
  initialUsers,
}: {
  name: string
  slug: string
  ownerType: 'org' | 'user'
  visibilityMode: 'public' | 'org' | 'restricted'
  allowContribute: boolean
  initialGroups: InitialGroup[]
  initialUsers: InitialUser[]
}) {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="space-y-1 text-sm">
          <p className="font-medium text-[var(--color-purple-deep)]">{name}</p>
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.knowledge_sharing_summary_docs_url({ slug })}
          </p>
          {ownerType === 'org' && (
            <>
              {visibilityMode !== 'restricted' && (
                <>
                  <p className="text-[var(--color-muted-foreground)]">
                    {m.knowledge_sharing_summary_org_default({
                      role: allowContribute ? 'contributor' : 'viewer',
                    })}
                  </p>
                  <p className="text-[var(--color-muted-foreground)]">
                    {allowContribute
                      ? m.knowledge_sharing_summary_contributors_yes()
                      : m.knowledge_sharing_summary_contributors_no()}
                  </p>
                </>
              )}
              {visibilityMode === 'restricted' &&
                (initialGroups.length > 0 || initialUsers.length > 0) && (
                  <>
                    <p className="text-[var(--color-muted-foreground)]">
                      {m.knowledge_sharing_summary_only_shared()}
                    </p>
                    {initialGroups.map((g) => (
                      <p
                        key={g.id}
                        className="text-xs text-[var(--color-muted-foreground)] pl-3"
                      >
                        &bull; {g.name} ({g.role})
                      </p>
                    ))}
                    {initialUsers.map((u) => (
                      <p
                        key={u.id}
                        className="text-xs text-[var(--color-muted-foreground)] pl-3"
                      >
                        &bull; {u.email} ({u.role})
                      </p>
                    ))}
                  </>
                )}
            </>
          )}
          <p className="text-[var(--color-muted-foreground)]">
            {m.knowledge_sharing_summary_docs_auto()}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
