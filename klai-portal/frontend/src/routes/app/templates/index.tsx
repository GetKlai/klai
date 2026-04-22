import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Sliders } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/templates/')({
  component: () => (
    <ProductGuard product="chat">
      <TemplatesPage />
    </ProductGuard>
  ),
})

interface Template {
  id: number
  name: string
  slug: string
  description: string | null
  prompt_text: string
  scope: string
  is_active: boolean
  created_by: string
}

function TemplatesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null)

  const { data, isLoading } = useQuery<Template[]>({
    queryKey: ['app-templates'],
    queryFn: async () => apiFetch<Template[]>('/api/app/templates'),
  })

  const deleteMutation = useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/app/templates/${slug}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
      setConfirmingDeleteId(null)
    },
  })

  const templates = data ?? []

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-[26px] font-display-bold text-gray-900">
          {m.templates_page_title()}
        </h1>
        <button
          type="button"
          onClick={() => void navigate({ to: '/app/templates/new' })}
          className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          {m.templates_new_button()}
        </button>
      </div>
      <p className="text-sm text-gray-400 mb-6">
        {m.templates_page_subtitle()}
      </p>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : templates.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <Sliders className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">
            {m.templates_empty_title()}
          </p>
          <p className="text-sm text-gray-400 mt-1 max-w-md mx-auto">
            {m.templates_empty_description()}
          </p>
          <button
            type="button"
            onClick={() => void navigate({ to: '/app/templates/new' })}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {m.templates_empty_cta()}
          </button>
        </div>
      ) : (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {templates.map((tpl) => {
            const isConfirming = confirmingDeleteId === tpl.id
            const isDeleting =
              deleteMutation.isPending && deleteMutation.variables === tpl.slug
            return (
              <div
                key={tpl.id}
                className="flex items-center gap-3 px-2 py-3.5 hover:bg-gray-50 transition-colors"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50">
                  <Sliders size={16} strokeWidth={1.75} className="text-gray-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-900 truncate">
                      {tpl.name}
                    </span>
                    <span className="rounded-full bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-700">
                      {tpl.scope === 'global'
                        ? m.templates_badge_organization()
                        : m.templates_badge_personal()}
                    </span>
                  </div>
                  {tpl.description ? (
                    <p className="text-xs text-gray-400 truncate mt-0.5">
                      {tpl.description}
                    </p>
                  ) : (
                    <p className="text-xs text-gray-400 truncate mt-0.5">
                      {tpl.prompt_text}
                    </p>
                  )}
                </div>
                <InlineDeleteConfirm
                  isConfirming={isConfirming}
                  isPending={isDeleting}
                  label={`Template "${tpl.name}" verwijderen?`}
                  cancelLabel={m.templates_delete_cancel()}
                  onConfirm={() => deleteMutation.mutate(tpl.slug)}
                  onCancel={() => setConfirmingDeleteId(null)}
                >
                  <div className="flex items-center gap-1 shrink-0">
                    <Tooltip label={m.templates_form_edit_title()}>
                      <button
                        type="button"
                        onClick={() =>
                          void navigate({
                            to: '/app/templates/$slug/edit',
                            params: { slug: tpl.slug },
                          })
                        }
                        aria-label={m.templates_form_edit_title()}
                        className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                      >
                        <Pencil size={14} />
                      </button>
                    </Tooltip>
                    <Tooltip label={m.templates_delete_button()}>
                      <button
                        type="button"
                        onClick={() => setConfirmingDeleteId(tpl.id)}
                        aria-label={m.templates_delete_button()}
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
