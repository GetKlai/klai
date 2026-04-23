import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Sliders, Trash2 } from 'lucide-react'

import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { Badge } from '@/components/ui/badge'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { useCurrentUser } from '@/hooks/useCurrentUser'
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
  scope: 'org' | 'personal'
  created_by: string
  is_active: boolean
  created_at: string
  updated_at: string
}

interface KBPref {
  active_template_ids: number[] | null
}

export function TemplatesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: currentUser } = useCurrentUser()
  const isAdmin = currentUser?.isAdmin ?? false
  const callerZitadelId = currentUser?.user_id

  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null)

  const templatesQuery = useQuery<Template[]>({
    queryKey: ['app-templates'],
    queryFn: async () => apiFetch<Template[]>('/api/app/templates'),
  })

  const prefQuery = useQuery<KBPref>({
    queryKey: ['kb-preference'],
    queryFn: async () => apiFetch<KBPref>('/api/app/account/kb-preference'),
  })

  const deleteMutation = useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/app/templates/${slug}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
      void queryClient.invalidateQueries({ queryKey: ['app-templates-for-bar'] })
      void queryClient.invalidateQueries({ queryKey: ['kb-preference'] })
      setConfirmingDeleteId(null)
    },
    onError: () => setConfirmingDeleteId(null),
  })

  const activeIds = new Set(prefQuery.data?.active_template_ids ?? [])

  function canMutate(t: Template): boolean {
    if (isAdmin) return true
    if (!callerZitadelId) return false
    return t.created_by === callerZitadelId
  }

  const createLabel = isAdmin
    ? m.templates_list_create_button()
    : m.templates_list_create_personal_button()

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="flex items-center justify-between mb-2">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.templates_page_title()}
        </h1>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void navigate({ to: '/app/templates/new' })}
            className="flex items-center gap-1.5 rounded-full bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {createLabel}
          </button>
        </div>
      </div>
      <p className="text-sm text-gray-400 mb-6">{m.templates_page_subtitle()}</p>

      {templatesQuery.isLoading && (
        <div className="space-y-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      )}

      {templatesQuery.isError && (
        <QueryErrorState error={templatesQuery.error ?? new Error('Unknown error')} onRetry={() => void templatesQuery.refetch()} />
      )}

      {templatesQuery.data && templatesQuery.data.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <Sliders className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">{m.templates_empty_title()}</p>
          <p className="text-sm text-gray-400 mt-1 max-w-md mx-auto">
            {m.templates_empty_description()}
          </p>
          <button
            type="button"
            onClick={() => void navigate({ to: '/app/templates/new' })}
            className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {m.templates_empty_cta()}
          </button>
        </div>
      )}

      {templatesQuery.data && templatesQuery.data.length > 0 && (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {templatesQuery.data.map((t) => {
            const mutateAllowed = canMutate(t)
            const isConfirming = confirmingDeleteId === t.id
            const isPending = deleteMutation.isPending && confirmingDeleteId === t.id
            const active = activeIds.has(t.id)

            return (
              <div key={t.id} className="flex items-start gap-4 py-3.5 px-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-900 truncate">{t.name}</span>
                    <Badge variant="secondary">
                      {t.scope === 'org'
                        ? m.templates_list_scope_org()
                        : m.templates_list_scope_personal()}
                    </Badge>
                    {active && (
                      <Badge variant="outline" className="border-green-500 text-green-700">
                        {m.templates_list_active_label()}
                      </Badge>
                    )}
                  </div>
                  {t.description && (
                    <p className="mt-1 text-sm text-gray-400 truncate">{t.description}</p>
                  )}
                </div>

                <InlineDeleteConfirm
                  isConfirming={isConfirming}
                  isPending={isPending}
                  label={m.templates_list_delete_confirm()}
                  cancelLabel={m.templates_form_cancel()}
                  onConfirm={() => deleteMutation.mutate(t.slug)}
                  onCancel={() => setConfirmingDeleteId(null)}
                >
                  <div className="flex items-center justify-end gap-1">
                    <button
                      type="button"
                      onClick={() => void navigate({ to: '/app/templates/$slug/edit', params: { slug: t.slug } })}
                      aria-label={m.templates_list_edit_label()}
                      title={m.templates_list_edit_label()}
                      className="p-2 rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-50 transition-colors"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      disabled={!mutateAllowed}
                      onClick={() => setConfirmingDeleteId(t.id)}
                      aria-label={m.templates_list_delete_label()}
                      title={
                        mutateAllowed
                          ? m.templates_list_delete_label()
                          : m.templates_form_scope_org_disabled_tooltip()
                      }
                      className="p-2 rounded-md text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-50 transition-colors disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-gray-400"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
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
