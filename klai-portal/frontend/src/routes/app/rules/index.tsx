import { createFileRoute } from '@tanstack/react-router'
import { Shield, MessageSquare, BookOpen, Lock } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'

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
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--color-foreground)]">
            {m.rules_page_title()}
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            {m.rules_page_subtitle()}
          </p>
        </div>
        <button
          type="button"
          disabled
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-300 cursor-not-allowed"
        >
          {m.rules_new_button()}
        </button>
      </div>

      {/* Empty state */}
      <div className="flex flex-col items-center gap-5 rounded-lg border border-gray-200 py-20 px-6">
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-gray-50">
          <Shield size={28} strokeWidth={1.5} className="text-gray-300" />
        </div>
        <div className="text-center space-y-2 max-w-sm">
          <p className="text-lg font-medium text-[var(--color-foreground)]">
            {m.rules_empty_title()}
          </p>
          <p className="text-sm text-gray-400 leading-relaxed">
            {m.rules_empty_description()}
          </p>
        </div>
        <button
          type="button"
          disabled
          className="rounded-lg border border-gray-200 px-5 py-2.5 text-sm text-gray-300 cursor-not-allowed"
        >
          {m.rules_empty_cta()}
        </button>
      </div>

      {/* How rules are enforced */}
      <div className="mt-10">
        <h2 className="mb-4 text-xs font-medium text-gray-400 uppercase tracking-wider">
          {m.rules_enforced_title()}
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="flex flex-col gap-2.5 rounded-lg border border-gray-200 p-5">
            <MessageSquare size={20} strokeWidth={1.5} className="text-gray-300" />
            <p className="text-sm font-medium text-[var(--color-foreground)]">Chat</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              {m.rules_enforced_chat()}
            </p>
          </div>
          <div className="flex flex-col gap-2.5 rounded-lg border border-gray-200 p-5">
            <BookOpen size={20} strokeWidth={1.5} className="text-gray-300" />
            <p className="text-sm font-medium text-[var(--color-foreground)]">Kennis</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              {m.rules_enforced_knowledge()}
            </p>
          </div>
          <div className="flex flex-col gap-2.5 rounded-lg border border-gray-200 p-5">
            <Lock size={20} strokeWidth={1.5} className="text-gray-300" />
            <p className="text-sm font-medium text-[var(--color-foreground)]">Privacy</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              {m.rules_enforced_privacy()}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
