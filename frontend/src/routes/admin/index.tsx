import { createFileRoute } from '@tanstack/react-router'
import { Users, CreditCard, Settings } from 'lucide-react'

export const Route = createFileRoute('/admin/')({
  component: AdminHome,
})

const adminSections = [
  {
    title: 'Gebruikers',
    description: 'Uitnodigen, rollen beheren en verwijderen',
    icon: Users,
    href: '/admin/users',
  },
  {
    title: 'Facturen',
    description: 'Abonnement en betaalhistorie',
    icon: CreditCard,
    href: '/admin/billing',
  },
  {
    title: 'Instellingen',
    description: 'Organisatienaam en accountdetails',
    icon: Settings,
    href: '/admin/settings',
  },
]

function AdminHome() {
  return (
    <div className="p-8 space-y-8 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          Beheer
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Gebruikers, abonnement en instellingen van jouw organisatie.
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
              className="text-[var(--color-purple-accent)]"
            />
            <div>
              <p className="text-sm font-medium text-[var(--color-purple-deep)] group-hover:text-[var(--color-purple-accent)] transition-colors">
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
