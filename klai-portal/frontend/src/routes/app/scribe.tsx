import { createFileRoute } from '@tanstack/react-router'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/scribe')({
  component: ScribePage,
})

function ScribePage() {
  return (
    <ProductGuard product="scribe">
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">{m.app_tool_scribe_title()}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.app_scribe_subtitle()}
        </p>
      </div>

    </div>
    </ProductGuard>
  )
}
