import { createFileRoute } from '@tanstack/react-router'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/scribe')({
  component: ScribePage,
})

function ScribePage() {
  return (
    <div className="p-8 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">{m.app_tool_scribe_title()}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.app_scribe_subtitle()}
        </p>
      </div>

    </div>
  )
}
