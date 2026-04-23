import { createFileRoute } from '@tanstack/react-router'
import { Shield } from 'lucide-react'
import { ProductGuard } from '@/components/layout/ProductGuard'
import * as m from '@/paraglide/messages'

// Phase 3 placeholder (SPEC-PORTAL-REDESIGN-002).
// Phase 4 replaces this with the full rules list + CRUD pages backed by
// /api/app/rules. For v1 the page communicates intent; enforcement on
// LiteLLM chat calls is a separate SPEC (SPEC-RULES-ENFORCEMENT-001).

export const Route = createFileRoute('/app/rules/')({
  component: () => (
    <ProductGuard product="chat">
      <RulesPage />
    </ProductGuard>
  ),
})

function RulesPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.rules_page_title()}
        </h1>
      </div>

      <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
        <Shield className="h-10 w-10 text-gray-300 mx-auto mb-3" />
        <p className="text-base font-medium text-gray-900">{m.rules_empty_title()}</p>
        <p className="text-sm text-gray-400 mt-1 max-w-md mx-auto">
          {m.rules_empty_description()}
        </p>
      </div>
    </div>
  )
}
