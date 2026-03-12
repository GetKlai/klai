import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'

export const Route = createFileRoute('/app/focus/')({
  component: FocusPage,
})

const FOCUS_BASE = '/research/v1'

interface Notebook {
  id: string
  name: string
  description: string | null
  scope: string
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

function FocusPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const { data, isLoading, error } = useQuery<NotebookListResponse>({
    queryKey: ['focus-notebooks', token],
    queryFn: async () => {
      const res = await fetch(`${FOCUS_BASE}/notebooks`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.app_focus_loading())
      return res.json()
    },
    enabled: !!token,
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.app_focus_source_delete() + ' mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-notebooks'] })
    },
  })

  const notebooks = data?.items ?? []

  const pageError =
    (error instanceof Error ? error.message : error ? m.app_focus_loading() : null) ??
    (deleteMutation.error instanceof Error
      ? deleteMutation.error.message
      : deleteMutation.error
        ? m.app_focus_source_delete() + ' mislukt'
        : null)

  return (
    <div className="p-8 space-y-6 max-w-4xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.app_tool_focus_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.app_focus_subtitle()}
          </p>
        </div>
        <Button onClick={() => navigate({ to: '/app/focus/new' })}>
          {m.app_focus_new_notebook()}
        </Button>
      </div>

      {pageError && (
        <p className="text-sm text-[var(--color-destructive)]">{pageError}</p>
      )}

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              {m.app_focus_loading()}
            </p>
          ) : notebooks.length === 0 ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              {m.app_focus_empty()}
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
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
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_focus_col_actions()}
                  </th>
                </tr>
              </thead>
              <tbody>
                {notebooks.map((nb, i) => (
                  <tr
                    key={nb.id}
                    className={
                      i % 2 === 0
                        ? 'bg-[var(--color-card)]'
                        : 'bg-[var(--color-secondary)]'
                    }
                  >
                    <td
                      className="px-6 py-3 text-[var(--color-purple-deep)] cursor-pointer hover:underline"
                      onClick={() =>
                        navigate({
                          to: '/app/focus/$notebookId',
                          params: { notebookId: nb.id },
                        })
                      }
                    >
                      <p className="font-medium">{nb.name}</p>
                      {nb.description && (
                        <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                          {nb.description}
                        </p>
                      )}
                    </td>
                    <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                      {nb.sources_count}
                    </td>
                    <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                      {nb.scope === 'personal'
                        ? m.app_focus_notebook_scope_personal()
                        : m.app_focus_notebook_scope_org()}
                    </td>
                    <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                      {formatDate(nb.created_at)}
                    </td>
                    <td className="px-6 py-3">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={
                          deleteMutation.isPending &&
                          deleteMutation.variables === nb.id
                        }
                        onClick={() => {
                          if (
                            window.confirm(
                              m.app_focus_notebook_delete_confirm({ name: nb.name }),
                            )
                          ) {
                            deleteMutation.mutate(nb.id)
                          }
                        }}
                      >
                        {m.app_focus_source_delete()}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
