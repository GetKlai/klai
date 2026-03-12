import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Plus, Trash2, Loader2, BookOpen } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/focus/')({
  component: FocusPage,
})

const FOCUS_BASE = '/research/v1'

interface Notebook {
  id: string
  name: string
  description: string | null
  scope: string
  default_mode: string
  sources_count: number
  created_at: string
  updated_at: string
}

interface NotebookListResponse {
  items: Notebook[]
  total: number
}

function FocusPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery<NotebookListResponse>({
    queryKey: ['focus-notebooks', token],
    queryFn: async () => {
      const res = await fetch(`${FOCUS_BASE}/notebooks`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
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
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-notebooks', token] })
    },
  })

  const notebooks = data?.items ?? []

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
          <Plus className="mr-2 h-4 w-4" />
          {m.app_focus_new_notebook()}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
        </div>
      ) : notebooks.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <BookOpen className="mx-auto mb-3 h-8 w-8 text-[var(--color-muted-foreground)]" />
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.app_focus_empty()}</p>
            <Button
              className="mt-4"
              onClick={() => navigate({ to: '/app/focus/new' })}
            >
              <Plus className="mr-2 h-4 w-4" />
              {m.app_focus_new_notebook()}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {notebooks.map((nb) => (
            <button
              key={nb.id}
              className="group text-left rounded-xl border bg-[var(--color-card)] p-5 transition-shadow hover:shadow-md"
              onClick={() => navigate({ to: '/app/focus/$notebookId', params: { notebookId: nb.id } })}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-sm text-[var(--color-purple-deep)] truncate group-hover:text-[var(--color-purple-accent)] transition-colors">
                    {nb.name}
                  </p>
                  {nb.description && (
                    <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)] line-clamp-2">
                      {nb.description}
                    </p>
                  )}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    deleteMutation.mutate(nb.id)
                  }}
                  disabled={deleteMutation.isPending && deleteMutation.variables === nb.id}
                  className="p-1 opacity-0 group-hover:opacity-100 text-[var(--color-muted-foreground)] hover:text-red-500 transition-all disabled:opacity-50 shrink-0"
                  aria-label={m.app_focus_source_delete()}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
              <p className="mt-3 text-xs text-[var(--color-muted-foreground)]">
                {nb.sources_count} {nb.sources_count === 1 ? 'bron' : 'bronnen'}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
