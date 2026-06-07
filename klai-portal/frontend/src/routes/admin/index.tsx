import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useAuth } from '@/lib/auth'
import { fetchMe } from '@/lib/api-me'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { ADMIN_NAV_ITEMS, adminNavItemIsVisible } from './-nav'

export const Route = createFileRoute('/admin/')({
  component: AdminHome,
})

function AdminHome() {
  const auth = useAuth()
  const { user } = useProtectedRoute({
    requireMinRole: 'kb_manager',
    noRoleFallback: '/app',
  })
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: auth.isAuthenticated,
  })

  // SPEC-PORTAL-UI-CONSISTENCY-001 REQ-4 / REQ-5: rows, not cards.
  const adminSections = ADMIN_NAV_ITEMS.filter((item) =>
    item.to !== '/admin' &&
    item.showOnOverview &&
    item.overviewDescription &&
    adminNavItemIsVisible(item, user?.effective_role, me),
  )

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-8">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_home_heading()}
        </h1>
        <p className="text-sm text-gray-400">
          {m.admin_home_subtitle()}
        </p>
      </div>

      <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
        {adminSections.map((section) => (
          <a
            key={section.to}
            href={section.to}
            className="group flex items-center gap-3 px-2 py-3.5 klai-hover"
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center text-gray-400">
              <section.icon className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-[15px] font-display text-gray-900">
                {section.label}
              </span>
              <p className="text-xs text-gray-400 mt-0.5">
                {section.overviewDescription}
              </p>
            </div>
            <ChevronRight className="h-4 w-4 text-gray-300 shrink-0" />
          </a>
        ))}
      </div>
    </div>
  )
}
