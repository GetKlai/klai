import { createFileRoute } from '@tanstack/react-router'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { PageContainer } from '@/components/ui/page-container'

export const Route = createFileRoute('/app/scribe')({
  component: ScribePage,
})

function ScribePage() {
  return (
    <ProductGuard product="scribe">
    <PageContainer width="3xl" gap="6">
      <div className="space-y-1">
        <h1 className="page-title text-[1.625rem] font-display-bold text-gray-900">{m.app_tool_scribe_title()}</h1>
        <p className="text-sm text-gray-600">
          {m.app_scribe_subtitle()}
        </p>
      </div>

    </PageContainer>
    </ProductGuard>
  )
}
