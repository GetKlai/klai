import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { ChevronRight } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { plural } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'

export const Route = createFileRoute('/admin/profiles/')({
  component: ProfilesPage,
})

interface AdminUser {
  zitadel_user_id: string
  role: ProfileRole
  status: 'active' | 'suspended' | 'offboarded'
}

function ProfilesPage() {
  const auth = useAuth()
  const msgs = m as unknown as Record<string, (() => string) | undefined>

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: AdminUser[] }>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })

  const counts = useMemo<Record<ProfileRole, number>>(() => {
    const initial: Record<ProfileRole, number> = {
      personal: 0,
      company: 0,
      kb_manager: 0,
      group_manager: 0,
      admin: 0,
    }
    if (!data?.users) return initial
    for (const user of data.users) {
      if (user.role in initial) {
        initial[user.role] += 1
      }
    }
    return initial
  }, [data])

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-6">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_profiles_title()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.admin_profiles_subtitle()}
        </p>
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
      ) : (
        <ul className="border-t border-b border-[var(--color-border)]">
          {PROFILE_LADDER.map((role) => {
            const labelFn = msgs[`profile_${role}_label`]
            const descFn = msgs[`profile_${role}_description`]
            const count = counts[role]
            const countLabel =
              plural(getLocale(), count) === 'one'
                ? m.admin_profiles_member_count_one()
                : m.admin_profiles_member_count_other({ count: String(count) })
            return (
              <li key={role} className="border-b border-[var(--color-border)] last:border-b-0">
                <Link
                  to="/admin/profiles/$profile"
                  params={{ profile: role }}
                  className="flex items-start gap-4 px-4 py-4 transition-colors hover:bg-[var(--color-muted)]"
                >
                  <div className="flex-1 space-y-0.5">
                    <p className="text-sm font-medium text-[var(--color-foreground)]">
                      {labelFn ? labelFn() : role}
                    </p>
                    <p className="text-xs text-[var(--color-muted-foreground)]">
                      {descFn ? descFn() : ''}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
                    <span>{countLabel}</span>
                    <ChevronRight className="h-4 w-4" />
                  </div>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
