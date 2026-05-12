import { createFileRoute } from '@tanstack/react-router'
import { Users, FolderKanban, CreditCard, Settings, Key, MessageSquare, ChevronRight } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/')({
  component: AdminHome,
})

function AdminHome() {
  // SPEC-PORTAL-UI-CONSISTENCY-001 REQ-4 / REQ-5: rows, not cards.
  const adminSections = [
    {
      title: m.admin_section_users_title(),
      description: m.admin_section_users_description(),
      icon: Users,
      href: '/admin/users',
    },
    {
      title: m.admin_section_groups_title(),
      description: m.admin_section_groups_description(),
      icon: FolderKanban,
      href: '/admin/groups',
    },
    {
      title: m.admin_section_api_keys_title(),
      description: m.admin_section_api_keys_description(),
      icon: Key,
      href: '/admin/api-keys',
    },
    {
      title: m.admin_section_widgets_title(),
      description: m.admin_section_widgets_description(),
      icon: MessageSquare,
      href: '/admin/widgets',
    },
    {
      title: m.admin_section_billing_title(),
      description: m.admin_section_billing_description(),
      icon: CreditCard,
      href: '/admin/billing',
    },
    {
      title: m.admin_section_settings_title(),
      description: m.admin_section_settings_description(),
      icon: Settings,
      href: '/admin/settings',
    },
  ]

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-8">
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
            key={section.title}
            href={section.href}
            className="group flex items-center gap-3 px-2 py-3.5 hover:bg-gray-50 transition-colors"
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400">
              <section.icon className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-[15px] font-display text-gray-900 group-hover:underline">
                {section.title}
              </span>
              <p className="text-xs text-gray-400 mt-0.5">
                {section.description}
              </p>
            </div>
            <ChevronRight className="h-4 w-4 text-gray-300 shrink-0" />
          </a>
        ))}
      </div>
    </div>
  )
}
