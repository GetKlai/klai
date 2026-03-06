import { createFileRoute } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/admin/users')({
  component: UsersPage,
})

function UsersPage() {
  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          Gebruikers
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Beheer de gebruikers in jouw organisatie.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Gebruikerslijst</CardTitle>
          <CardDescription>
            Uitnodigen en rollen beheren — wordt ingebouwd zodra portal_users live is.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-muted-foreground)]">Placeholder</p>
        </CardContent>
      </Card>
    </div>
  )
}
