import { createFileRoute } from '@tanstack/react-router'
import { Sliders } from 'lucide-react'
import { ProductGuard } from '@/components/layout/ProductGuard'
import * as m from '@/paraglide/messages'

// Phase 3 placeholder (SPEC-PORTAL-REDESIGN-002).
// Phase 5 replaces this with the full templates list + CRUD pages backed by
// /api/app/templates. Active-template injection into chat calls is a separate
// SPEC (SPEC-TEMPLATES-INJECTION-001).

export const Route = createFileRoute('/app/templates/')({
  component: () => (
    <ProductGuard product="chat">
      <TemplatesPage />
    </ProductGuard>
  ),
})

function TemplatesPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.templates_page_title()}
        </h1>
      </div>

      <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
        <Sliders className="h-10 w-10 text-gray-300 mx-auto mb-3" />
        <p className="text-base font-medium text-gray-900">{m.templates_empty_title()}</p>
        <p className="text-sm text-gray-400 mt-1 max-w-md mx-auto">
          {m.templates_empty_description()}
        </p>
      </div>
    </div>
  )
}
