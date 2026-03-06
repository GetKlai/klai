import { createFileRoute } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/app/scribe')({
  component: ScribePage,
})

function ScribePage() {
  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Scribe</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Documenten en notities genereren met AI.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Scribe</CardTitle>
          <CardDescription>Document-editor wordt hier ingebouwd — Phase 2.</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-muted-foreground)]">Placeholder</p>
        </CardContent>
      </Card>
    </div>
  )
}
