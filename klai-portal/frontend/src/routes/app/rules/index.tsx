import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Shield } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { Badge } from '@/components/ui/badge'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/rules/')({
  component: () => (
    <ProductGuard product="chat">
      <RulesPage />
    </ProductGuard>
  ),
})

// Rules zijn strict guardrails (block / redact). Geen 'instruction' type —
// prompt-instructies horen bij Templates, niet Rules.
type RuleType = 'pii_block' | 'pii_redact' | 'keyword_block' | 'keyword_redact'

interface Rule {
  id: number
  name: string
  slug: string
  description: string | null
  rule_text: string
  scope: string
  rule_type: RuleType
  is_active: boolean
  created_by: string
}

function getRuleTypeLabel(type: RuleType): string {
  switch (type) {
    case 'pii_block':
      return m.rules_type_pii_block()
    case 'pii_redact':
      return m.rules_type_pii_redact()
    case 'keyword_block':
      return m.rules_type_keyword_block()
    case 'keyword_redact':
      return m.rules_type_keyword_redact()
  }
}

function getRuleTypeBadgeVariant(
  type: RuleType,
): 'destructive' | 'warning' {
  switch (type) {
    case 'pii_block':
    case 'keyword_block':
      return 'destructive'
    case 'pii_redact':
    case 'keyword_redact':
      return 'warning'
  }
}

function RulesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null)

  const { data, isLoading } = useQuery<Rule[]>({
    queryKey: ['app-rules'],
    queryFn: async () => apiFetch<Rule[]>('/api/app/rules'),
  })

  const deleteMutation = useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/app/rules/${slug}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      setConfirmingDeleteId(null)
    },
  })

  const rules = data ?? []

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-[26px] font-display-bold text-gray-900">
          {m.rules_page_title()}
        </h1>
        <button
          type="button"
          onClick={() => void navigate({ to: '/app/rules/new' })}
          className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          {m.rules_new_button()}
        </button>
      </div>
      <p className="text-sm text-gray-400 mb-6">
        {m.rules_page_subtitle()}
      </p>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : rules.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <Shield className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">{m.rules_empty_title()}</p>
          <p className="text-sm text-gray-400 mt-1 max-w-md mx-auto">
            {m.rules_empty_description()}
          </p>
          <button
            type="button"
            onClick={() => void navigate({ to: '/app/rules/new' })}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {m.rules_empty_cta()}
          </button>
        </div>
      ) : (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {rules.map((rule) => {
            const isConfirming = confirmingDeleteId === rule.id
            const isDeleting =
              deleteMutation.isPending && deleteMutation.variables === rule.slug
            return (
              <div
                key={rule.id}
                className="flex items-center gap-3 px-2 py-3.5 hover:bg-gray-50 transition-colors"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50">
                  <Shield size={16} strokeWidth={1.75} className="text-gray-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-900 truncate">
                      {rule.name}
                    </span>
                    <Badge
                      variant={getRuleTypeBadgeVariant(rule.rule_type)}
                      className="text-[10px] uppercase tracking-wide"
                    >
                      {getRuleTypeLabel(rule.rule_type)}
                    </Badge>
                    <span className="rounded-full bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-700">
                      {rule.scope === 'global'
                        ? m.rules_badge_organization()
                        : m.rules_badge_personal()}
                    </span>
                  </div>
                  {rule.description && (
                    <p className="text-xs text-gray-400 truncate mt-0.5">
                      {rule.description}
                    </p>
                  )}
                </div>
                <InlineDeleteConfirm
                  isConfirming={isConfirming}
                  isPending={isDeleting}
                  label={`Regel "${rule.name}" verwijderen?`}
                  cancelLabel={m.rules_delete_cancel()}
                  onConfirm={() => deleteMutation.mutate(rule.slug)}
                  onCancel={() => setConfirmingDeleteId(null)}
                >
                  <div className="flex items-center gap-1 shrink-0">
                    <Tooltip label={m.rules_form_edit_title()}>
                      <button
                        type="button"
                        onClick={() =>
                          void navigate({
                            to: '/app/rules/$slug/edit',
                            params: { slug: rule.slug },
                          })
                        }
                        aria-label={m.rules_form_edit_title()}
                        className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                      >
                        <Pencil size={14} />
                      </button>
                    </Tooltip>
                    <Tooltip label={m.rules_delete_button()}>
                      <button
                        type="button"
                        onClick={() => setConfirmingDeleteId(rule.id)}
                        aria-label={m.rules_delete_button()}
                        className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors"
                      >
                        <Trash2 size={14} />
                      </button>
                    </Tooltip>
                  </div>
                </InlineDeleteConfirm>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
