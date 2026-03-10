import { createFileRoute } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/transcribe')({
  component: TranscribePage,
})

function TranscribePage() {
  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.app_tool_transcribe_title()}
        </h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.app_transcribe_subtitle()}
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{m.app_transcribe_card_title()}</CardTitle>
          <CardDescription>{m.app_transcribe_card_description()}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.app_transcribe_placeholder()}</p>
        </CardContent>
      </Card>
    </div>
  )
}
