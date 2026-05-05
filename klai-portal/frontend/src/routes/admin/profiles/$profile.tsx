import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ArrowLeft, Loader2, MoreHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { adminLogger } from '@/lib/logger'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'

export const Route = createFileRoute('/admin/profiles/$profile')({
  component: ProfileDrillInPage,
  beforeLoad: ({ params }) => {
    if (!PROFILE_LADDER.includes(params.profile as ProfileRole)) {
      throw new Error(`Unknown profile: ${params.profile}`)
    }
  },
})

interface AdminUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: ProfileRole
  status: 'active' | 'suspended' | 'offboarded'
  invite_pending: boolean
}

function profileLabel(role: ProfileRole): string {
  const msgs = m as unknown as Record<string, (() => string) | undefined>
  const labelFn = msgs[`profile_${role}_label`]
  return labelFn ? labelFn() : role
}

function ProfileDrillInPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const { profile } = Route.useParams()
  const profileRole = profile as ProfileRole

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: AdminUser[] }>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })

  const usersInProfile = useMemo(() => {
    return (data?.users ?? []).filter((u) => u.role === profileRole)
  }, [data, profileRole])

  // SPEC-PORTAL-ADMIN-UI-001 REQ-6: single-user move via PATCH /role.
  const moveMutation = useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: ProfileRole }) => {
      await apiFetch(`/api/admin/users/${userId}/role`, {
        method: 'PATCH',
        body: JSON.stringify({ role }),
      })
    },
    onSuccess: (_data, vars) => {
      adminLogger.info('Profile changed (drill-in)', { userId: vars.userId, role: vars.role })
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
    onError: (err) => {
      setErrorMessage(err instanceof Error ? err.message : m.admin_profiles_error_change())
    },
  })

  // SPEC-PORTAL-ADMIN-UI-001 REQ-7: bulk move — frontend loops PATCH /role.
  // No bulk endpoint in v1; SPEC defers that to a follow-up SPEC.
  const bulkMoveMutation = useMutation({
    mutationFn: async ({ userIds, role }: { userIds: string[]; role: ProfileRole }) => {
      const results = await Promise.allSettled(
        userIds.map((userId) =>
          apiFetch(`/api/admin/users/${userId}/role`, {
            method: 'PATCH',
            body: JSON.stringify({ role }),
          }),
        ),
      )
      const failures = results.filter((r) => r.status === 'rejected')
      if (failures.length > 0) {
        const firstError = failures[0]
        throw firstError.reason instanceof Error
          ? firstError.reason
          : new Error(String(firstError.reason))
      }
    },
    onSuccess: (_data, vars) => {
      adminLogger.info('Bulk profile changed', { count: vars.userIds.length, role: vars.role })
      setSelectedIds(new Set())
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
    onError: (err) => {
      setErrorMessage(err instanceof Error ? err.message : m.admin_profiles_error_change())
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const otherProfiles = PROFILE_LADDER.filter((r) => r !== profileRole)
  const selectedCount = selectedIds.size
  const allSelected = usersInProfile.length > 0 && selectedCount === usersInProfile.length

  function toggleOne(userId: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(userId)) {
        next.delete(userId)
      } else {
        next.add(userId)
      }
      return next
    })
  }

  function toggleAll() {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(usersInProfile.map((u) => u.zitadel_user_id)))
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-6">
      <div className="space-y-2">
        <Link
          to="/admin/profiles"
          className="inline-flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-foreground)]"
        >
          <ArrowLeft className="h-4 w-4" />
          {m.admin_profiles_back()}
        </Link>
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {profileLabel(profileRole)}
        </h1>
      </div>

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_profiles_loading()}
        </p>
      ) : usersInProfile.length === 0 ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_profiles_drill_in_empty()}
        </p>
      ) : (
        <>
          {errorMessage && (
            <p className="text-sm text-[var(--color-destructive)]">{errorMessage}</p>
          )}

          {selectedCount > 0 && (
            <div className="flex items-center gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-muted)] px-3 py-2">
              <span className="text-sm text-[var(--color-foreground)]">
                {selectedCount}
              </span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="sm" disabled={bulkMoveMutation.isPending}>
                    {bulkMoveMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                    {m.admin_profiles_bulk_move_button({ count: String(selectedCount) })}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  {otherProfiles.map((target) => (
                    <DropdownMenuItem
                      key={target}
                      onClick={() => {
                        setErrorMessage(null)
                        bulkMoveMutation.mutate({
                          userIds: [...selectedIds],
                          role: target,
                        })
                      }}
                    >
                      {profileLabel(target)}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button size="sm" variant="ghost" onClick={() => setSelectedIds(new Set())}>
                {m.admin_users_cancel()}
              </Button>
            </div>
          )}

          <table className="w-full text-sm border-t border-b border-[var(--color-border)]">
            <thead>
              <tr className="border-b border-[var(--color-border)]">
                <th className="py-3 pl-2 pr-2 text-left">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    aria-label={m.admin_profiles_select_all()}
                    className="accent-[var(--color-accent)]"
                  />
                </th>
                <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                  {m.admin_users_col_name()}
                </th>
                <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                  {m.admin_users_col_email()}
                </th>
                <th className="py-3 pr-4 text-right text-xs font-medium text-gray-400 tracking-wide">
                  {m.admin_users_col_actions()}
                </th>
              </tr>
            </thead>
            <tbody>
              {usersInProfile.map((user) => {
                const isSelected = selectedIds.has(user.zitadel_user_id)
                const isMoving =
                  moveMutation.isPending &&
                  moveMutation.variables?.userId === user.zitadel_user_id
                return (
                  <tr
                    key={user.zitadel_user_id}
                    className="border-b border-[var(--color-border)] last:border-b-0"
                  >
                    <td className="py-4 pl-2 pr-2 align-top">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleOne(user.zitadel_user_id)}
                        aria-label={`Select ${user.email}`}
                        className="accent-[var(--color-accent)]"
                      />
                    </td>
                    <td className="py-4 pr-4 align-top text-[var(--color-foreground)]">
                      {`${user.first_name} ${user.last_name}`.trim() || '—'}
                    </td>
                    <td className="py-4 pr-4 align-top text-[var(--color-foreground)]">
                      {user.email}
                    </td>
                    <td className="py-4 pr-4 align-top">
                      <div className="flex justify-end">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button
                              aria-label={m.admin_profiles_move_to()}
                              disabled={isMoving}
                              className="inline-flex items-center justify-center rounded text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-secondary)] disabled:opacity-40"
                            >
                              {isMoving ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <MoreHorizontal className="h-4 w-4" />
                              )}
                            </button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            {otherProfiles.map((target) => (
                              <DropdownMenuItem
                                key={target}
                                onClick={() => {
                                  setErrorMessage(null)
                                  moveMutation.mutate({
                                    userId: user.zitadel_user_id,
                                    role: target,
                                  })
                                }}
                              >
                                {m.admin_profiles_move_to()} {profileLabel(target)}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}

    </div>
  )
}
