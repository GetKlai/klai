import { createFileRoute } from '@tanstack/react-router'
import { Users, FolderKanban, CreditCard, Settings, Key, MessageSquare } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/')({
  component: AdminHome,
})

function AdminHome() {
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
    <div className="p-6 space-y-8 max-w-3xl">
      <div className="space-y-1">
        <h1 className="page-title text-xl/none font-semibold text-gray-900">
          {m.admin_home_heading()}
        </h1>
        <p className="text-sm text-gray-400">
          {m.admin_home_subtitle()}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {adminSections.map((section) => (
          <a
            key={section.title}
            href={section.href}
            className="group flex flex-col gap-3 rounded-lg border bg-white p-5 transition-shadow hover:shadow-md"
          >
            <section.icon
              size={20}
              strokeWidth={1.5}
              className="text-gray-400"
            />
            <div>
              <p className="text-sm font-medium text-gray-900 group-hover:text-gray-700 transition-colors">
                {section.title}
              </p>
              <p className="mt-0.5 text-xs text-gray-400">
                {section.description}
              </p>
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}
