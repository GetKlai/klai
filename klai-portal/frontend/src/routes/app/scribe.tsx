import { createFileRoute } from '@tanstack/react-router'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/scribe')({
  component: ScribePage,
})

function ScribePage() {
  return (
    <ProductGuard product="scribe">
    <div className="p-6 space-y-6 max-w-3xl">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold text-gray-900">{m.app_tool_scribe_title()}</h1>
        <p className="text-sm text-gray-400">
          {m.app_scribe_subtitle()}
        </p>
      </div>

    </div>
    </ProductGuard>
  )
}
