import { createFileRoute } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export const Route = createFileRoute('/app/transcribe')({
  component: TranscribePage,
})

function TranscribePage() {
  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          Transcriberen
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Audio en video omzetten naar tekst.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Transcriptie</CardTitle>
          <CardDescription>Upload-interface wordt hier ingebouwd — Phase 2.</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-muted-foreground)]">Placeholder</p>
        </CardContent>
      </Card>
    </div>
  )
}
