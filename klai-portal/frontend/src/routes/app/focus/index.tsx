import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Tooltip } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Input } from '@/components/ui/input'
import { Plus, Loader2, Trash2, Check, X, BookOpen, Pencil } from 'lucide-react'
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
    <div className="p-12 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-[var(--color-foreground)]">
            {m.app_tool_focus_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && countLabel}
          </p>
        </div>
        <Button data-help-id="focus-add" onClick={() => navigate({ to: '/app/focus/new' })}>
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

      <Card data-help-id="focus-list">
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : notebooks.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <BookOpen className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
              <div className="space-y-1">
                <p className="font-medium text-[var(--color-foreground)]">
                  {m.app_focus_empty_heading()}
                </p>
                <p className="text-sm text-[var(--color-muted-foreground)]">
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
            <>
              <div className="px-4 pt-3 pb-2 border-b border-[var(--color-border)]">
                <Input
                  value={search}
                  onChange={(e) => void navigate({ search: { search: e.target.value || undefined } })}
                  placeholder={m.app_focus_search_placeholder()}
                  className="h-8 text-sm max-w-xs"
                />
              </div>
              {filteredNotebooks.length === 0 ? (
                <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
                  {m.app_focus_search_empty()}
                </p>
              ) : (
                <table className="w-full text-sm table-fixed">
                  <thead>
                    <tr className="border-b border-[var(--color-border)]">
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-1/2">
                        {m.app_focus_notebook_name_label()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_focus_sources_heading()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_focus_notebook_scope_label()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_focus_col_created()}
                      </th>
                      <th className="px-3 py-3 w-20" />
                    </tr>
                  </thead>
                  <tbody>
                    {filteredNotebooks.map((nb, i) => {
                      const isConfirmingDelete = confirmingDeleteId === nb.id
                      const isDeleting = deleteMutation.isPending && deleteMutation.variables === nb.id

                      return (
                        <tr
                          key={nb.id}
                          className={
                            i % 2 === 0
                              ? 'bg-[var(--color-card)]'
                              : 'bg-[var(--color-secondary)]'
                          }
                        >
                          <td
                            className="px-6 py-3 text-[var(--color-foreground)] cursor-pointer hover:underline"
                            onClick={() =>
                              navigate({
                                to: '/app/focus/$notebookId',
                                params: { notebookId: nb.id },
                              })
                            }
                          >
                            <p className="font-medium truncate">{nb.name}</p>
                            {nb.description && (
                              <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5 truncate">
                                {nb.description}
                              </p>
                            )}
                          </td>
                          <td className="px-6 py-3 text-[var(--color-muted-foreground)] tabular-nums">
                            {nb.sources_count}
                          </td>
                          <td className="px-6 py-3 text-[var(--color-foreground)]">
                            {nb.scope === 'personal'
                              ? m.app_focus_notebook_scope_personal()
                              : m.app_focus_notebook_scope_org()}
                          </td>
                          <td className="px-6 py-3 text-[var(--color-foreground)]">
                            {formatDate(nb.created_at)}
                          </td>
                          <td className="px-3 py-3 w-20 text-right">
                            {(nb.scope !== 'org' || isOrgAdmin || nb.owner_user_id === currentUserId) && (isConfirmingDelete ? (
                              <div className="flex items-center justify-end gap-1">
                                {isDeleting ? (
                                  <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                                ) : (
                                  <>
                                    <button
                                      onClick={() => deleteMutation.mutate(nb.id)}
                                      aria-label={m.app_focus_delete_confirm_action()}
                                      className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-destructive)] text-white transition-colors hover:opacity-90"
                                    >
                                      <Check className="h-3.5 w-3.5" />
                                    </button>
                                    <button
                                      onClick={() => setConfirmingDeleteId(null)}
                                      aria-label={m.app_focus_delete_cancel_action()}
                                      className="flex h-7 w-7 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-border)]"
                                    >
                                      <X className="h-3.5 w-3.5" />
                                    </button>
                                  </>
                                )}
                              </div>
                            ) : (
                              <div className="flex items-center justify-end gap-1">
                                <Tooltip label={m.app_focus_edit_label()}>
                                  <button
                                    onClick={() =>
                                      navigate({
                                        to: '/app/focus/$notebookId/edit',
                                        params: { notebookId: nb.id },
                                      })
                                    }
                                    aria-label={m.app_focus_edit_label()}
                                    className="flex h-7 w-7 items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                                  >
                                    <Pencil className="h-3.5 w-3.5" />
                                  </button>
                                </Tooltip>
                                <Tooltip label={m.app_focus_delete_label()}>
                                  <button
                                    onClick={() => setConfirmingDeleteId(nb.id)}
                                    aria-label={m.app_focus_delete_label()}
                                    className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </Tooltip>
                              </div>
                            ))}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </CardContent>
      </Card>
      </>}
    </div>
  )
}
