import { createFileRoute } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/admin/settings')({
  component: AdminSettingsPage,
})

function AdminSettingsPage() {
  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          Instellingen
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Organisatienaam en accountdetails.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Organisatie</CardTitle>
          <CardDescription>
            Naam, domein en abonnementsdetails — wordt ingebouwd in Phase 3.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-muted-foreground)]">Placeholder</p>
        </CardContent>
      </Card>
    </div>
  )
}
