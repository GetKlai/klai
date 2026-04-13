import { createFileRoute } from '@tanstack/react-router'
import { Shield, MessageSquare, BookOpen, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
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
    <div className="p-6 space-y-8 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {m.rules_page_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.rules_page_subtitle()}
          </p>
        </div>
        <Button size="sm" disabled>
          {m.rules_new_button()}
        </Button>
      </div>

      {/* Empty state */}
      <div className="flex flex-col items-center gap-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] py-16 px-6">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-rl-accent)]/10">
          <Shield size={24} strokeWidth={1.5} className="text-[var(--color-rl-accent)]" />
        </div>
        <div className="text-center space-y-2 max-w-md">
          <p className="font-medium text-[var(--color-foreground)]">
            {m.rules_empty_title()}
          </p>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.rules_empty_description()}
          </p>
        </div>
        <Button disabled>
          {m.rules_empty_cta()}
        </Button>
      </div>

      {/* How rules are enforced */}
      <div className="space-y-4">
        <h2 className="text-sm font-medium text-[var(--color-muted-foreground)] uppercase tracking-[0.04em]">
          {m.rules_enforced_title()}
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="flex flex-col gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
            <MessageSquare size={20} strokeWidth={1.5} className="text-[var(--color-rl-accent)]" />
            <p className="text-sm font-medium text-[var(--color-foreground)]">Chat</p>
            <p className="text-xs text-[var(--color-muted-foreground)]">
              {m.rules_enforced_chat()}
            </p>
          </div>
          <div className="flex flex-col gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
            <BookOpen size={20} strokeWidth={1.5} className="text-[var(--color-rl-accent)]" />
            <p className="text-sm font-medium text-[var(--color-foreground)]">Kennis</p>
            <p className="text-xs text-[var(--color-muted-foreground)]">
              {m.rules_enforced_knowledge()}
            </p>
          </div>
          <div className="flex flex-col gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
            <Lock size={20} strokeWidth={1.5} className="text-[var(--color-rl-accent)]" />
            <p className="text-sm font-medium text-[var(--color-foreground)]">Privacy</p>
            <p className="text-xs text-[var(--color-muted-foreground)]">
              {m.rules_enforced_privacy()}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
