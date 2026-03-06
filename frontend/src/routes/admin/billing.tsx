import { createFileRoute } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/admin/billing')({
  component: BillingPage,
})

function BillingPage() {
  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Facturen</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Abonnement en betaalhistorie.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Factuuroverzicht</CardTitle>
          <CardDescription>
            Via Moneybird — link wordt gegenereerd zodra abonnement actief is.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-muted-foreground)]">Placeholder</p>
        </CardContent>
      </Card>
    </div>
  )
}
