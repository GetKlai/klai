import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Tooltip } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Input } from '@/components/ui/input'
import { Plus, Loader2, Trash2, BookOpen, Pencil } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { ProductGuard } from '@/components/layout/ProductGuard'

type FocusSearch = { search?: string }

export const Route = createFileRoute('/app/focus/')({
  validateSearch: (search: Record<string, unknown>): FocusSearch => ({
    search: typeof search.search === 'string' && search.search ? search.search : undefined,
  }),
  component: () => (
    <ProductGuard product="chat">
      <FocusPage />
    </ProductGuard>
  ),
})

const FOCUS_BASE = '/research/v1'

interface Notebook {
  id: string
  name: string
  description: string | null
  scope: string
  owner_user_id: string
  sources_count: number
  created_at: string
}

interface NotebookListResponse {
  items: Notebook[]
  total: number
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function parseJwtRoles(token: string): string[] {
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    const rolesRecord = payload['urn:zitadel:iam:org:project:roles']
    return rolesRecord && typeof rolesRecord === 'object' ? Object.keys(rolesRecord) : []
  } catch {
    return []
  }
}

function FocusPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate({ from: '/app/focus/' })

  const isOrgAdmin = token ? parseJwtRoles(token).includes('org_admin') : false
  const currentUserId = auth.user?.profile?.sub

  const { search: searchParam } = Route.useSearch()
  const search = searchParam ?? ''

  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)

  const { data, isLoading, error, refetch } = useQuery<NotebookListResponse>({
    queryKey: ['focus-notebooks'],
    queryFn: async () => apiFetch<NotebookListResponse>(`${FOCUS_BASE}/notebooks`, token),
    enabled: !!token,
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`${FOCUS_BASE}/notebooks/${id}`, token, { method: 'DELETE' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['focus-notebooks'] })
      setConfirmingDeleteId(null)
    },
  })

  const notebooks = data?.items ?? []
  const filteredNotebooks = search.trim()
    ? notebooks.filter((nb) => {
        const q = search.toLowerCase()
        return nb.name.toLowerCase().includes(q) || (nb.description?.toLowerCase().includes(q) ?? false)
      })
    : notebooks

  const countLabel =
    data?.total === 1
      ? m.app_focus_count_one()
      : m.app_focus_count({ count: String(data?.total ?? 0) })

  const mutationError =
    deleteMutation.error instanceof Error
      ? deleteMutation.error.message
      : deleteMutation.error
        ? m.app_focus_delete_label() + ' mislukt'
        : null

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-gray-900">
            {m.app_tool_focus_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {!isLoading && countLabel}
          </p>
        </div>
        <Button size="sm" data-help-id="focus-add" onClick={() => navigate({ to: '/app/focus/new' })}>
          <Plus className="mr-2 h-4 w-4" />
          {m.app_focus_new_notebook()}
        </Button>
      </div>

      {error ? (
        <QueryErrorState error={error instanceof Error ? error : new Error(String(error))} onRetry={() => void refetch()} />
      ) : <>
      {mutationError && (
        <p className="text-sm text-[var(--color-destructive)]">{mutationError}</p>
      )}

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
        </div>
      ) : notebooks.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <BookOpen className="h-10 w-10 text-gray-400 opacity-40" />
          <div className="space-y-1">
            <p className="font-medium text-gray-900">
              {m.app_focus_empty_heading()}
            </p>
            <p className="text-sm text-gray-400">
              {m.app_focus_empty_body()}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate({ to: '/app/focus/new' })}
            className="mt-2"
          >
            <Plus className="mr-2 h-3.5 w-3.5" />
            {m.app_focus_new_notebook()}
          </Button>
        </div>
      ) : (
        <div data-help-id="focus-list">
          <div className="mb-4">
            <Input
              value={search}
              onChange={(e) => void navigate({ search: { search: e.target.value || undefined } })}
              placeholder={m.app_focus_search_placeholder()}
              className="h-8 text-sm max-w-xs"
            />
          </div>
          {filteredNotebooks.length === 0 ? (
            <p className="py-8 text-sm text-gray-400">
              {m.app_focus_search_empty()}
            </p>
          ) : (
            <table className="w-full text-sm table-fixed border-t border-b border-gray-200">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em]">
                    {m.app_focus_notebook_name_label()}
                  </th>
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-24">
                    {m.app_focus_sources_heading()}
                  </th>
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-32">
                    {m.app_focus_notebook_scope_label()}
                  </th>
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-28">
                    {m.app_focus_col_created()}
                  </th>
                  <th className="py-3 text-right w-20" />
                </tr>
              </thead>
              <tbody>
                {filteredNotebooks.map((nb) => {
                  const isConfirmingDelete = confirmingDeleteId === nb.id
                  const isDeleting = deleteMutation.isPending && deleteMutation.variables === nb.id

                  return (
                    <tr
                      key={nb.id}
                      className="border-b border-gray-200 last:border-b-0"
                    >
                      <td
                        className="py-4 pr-4 align-top text-gray-900 cursor-pointer hover:underline"
                        onClick={() =>
                          navigate({
                            to: '/app/focus/$notebookId',
                            params: { notebookId: nb.id },
                          })
                        }
                      >
                        <p className="font-medium truncate">{nb.name}</p>
                        {nb.description && (
                          <p className="text-xs text-gray-400 mt-0.5 truncate">
                            {nb.description}
                          </p>
                        )}
                      </td>
                      <td className="py-4 pr-4 align-top text-gray-400 tabular-nums w-24">
                        {nb.sources_count}
                      </td>
                      <td className="py-4 pr-4 align-top text-gray-900 w-32">
                        {nb.scope === 'personal'
                          ? m.app_focus_notebook_scope_personal()
                          : m.app_focus_notebook_scope_org()}
                      </td>
                      <td className="py-4 pr-4 align-top text-gray-900 whitespace-nowrap tabular-nums w-28">
                        {formatDate(nb.created_at)}
                      </td>
                      <td className="py-4 align-top text-right w-20">
                        {(nb.scope !== 'org' || isOrgAdmin || nb.owner_user_id === currentUserId) && (
                          <InlineDeleteConfirm
                            isConfirming={isConfirmingDelete}
                            isPending={isDeleting}
                            label={m.app_focus_delete_confirm({ name: nb.name })}
                            cancelLabel={m.app_focus_delete_cancel_action()}
                            onConfirm={() => deleteMutation.mutate(nb.id)}
                            onCancel={() => setConfirmingDeleteId(null)}
                          >
                            <div className="flex items-start justify-end gap-2 mt-px">
                              <Tooltip label={m.app_focus_edit_label()}>
                                <button
                                  onClick={() =>
                                    navigate({
                                      to: '/app/focus/$notebookId/edit',
                                      params: { notebookId: nb.id },
                                    })
                                  }
                                  aria-label={m.app_focus_edit_label()}
                                  className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                                >
                                  <Pencil className="h-4 w-4" />
                                </button>
                              </Tooltip>
                              <Tooltip label={m.app_focus_delete_label()}>
                                <button
                                  onClick={() => setConfirmingDeleteId(nb.id)}
                                  aria-label={m.app_focus_delete_label()}
                                  className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              </Tooltip>
                            </div>
                          </InlineDeleteConfirm>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
      </>}
    </div>
  )
}
