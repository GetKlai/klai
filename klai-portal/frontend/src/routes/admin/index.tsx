import { createFileRoute } from '@tanstack/react-router'
import { Users, FolderKanban, CreditCard, Settings } from 'lucide-react'
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
    <div className="p-8 space-y-8 max-w-3xl">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold text-[var(--color-foreground)]">
          {m.admin_home_heading()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.admin_home_subtitle()}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {adminSections.map((section) => (
          <a
            key={section.title}
            href={section.href}
            className="group flex flex-col gap-3 rounded-xl border bg-[var(--color-card)] p-5 transition-shadow hover:shadow-md"
          >
            <section.icon
              size={20}
              strokeWidth={1.5}
              className="text-[var(--color-rl-accent)]"
            />
            <div>
              <p className="text-sm font-medium text-[var(--color-foreground)] group-hover:text-[var(--color-rl-accent)] transition-colors">
                {section.title}
              </p>
              <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
                {section.description}
              </p>
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}
