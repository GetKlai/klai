import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Plus, Loader2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { DeleteConfirmButton } from '@/components/ui/delete-confirm-button'

export const Route = createFileRoute('/app/transcribe/')({
  component: TranscribePage,
})

const SCRIBE_BASE = '/scribe/v1'

interface TranscriptionItem {
  id: string
  text: string
  language: string
  duration_seconds: number
  created_at: string
}

interface TranscriptionListResponse {
  items: TranscriptionItem[]
  total: number
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function TranscribePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const { data, isLoading } = useQuery<TranscriptionListResponse>({
    queryKey: ['transcriptions', token],
    queryFn: async () => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions?limit=50`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      return res.json()
    },
    enabled: !!token,
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcriptions', token] })
    },
  })

  const items = data?.items ?? []

  return (
    <div className="p-8 space-y-6 max-w-4xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.app_tool_transcribe_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && m.app_transcribe_count({ count: String(data?.total ?? 0) })}
          </p>
        </div>
        <Button onClick={() => navigate({ to: '/app/transcribe/add' })}>
          <Plus className="mr-2 h-4 w-4" />
          {m.app_transcribe_add_button()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : items.length === 0 ? (
            <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
              {m.app_transcribe_history_empty()}
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_transcribe_col_text()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_transcribe_col_words()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_transcribe_col_language()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_transcribe_col_duration()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.app_transcribe_col_date()}
                  </th>
                  <th className="px-6 py-3" />
                </tr>
              </thead>
              <tbody>
                {items.map((item, i) => (
                  <tr
                    key={item.id}
                    className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                  >
                    <td className="px-6 py-3 text-[var(--color-purple-deep)] max-w-xs">
                      <span className="block truncate">{item.text}</span>
                    </td>
                    <td className="px-6 py-3 text-[var(--color-muted-foreground)] tabular-nums">
                      {item.text.trim().split(/\s+/).filter(Boolean).length.toLocaleString()}
                    </td>
                    <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                      {item.language.toUpperCase()}
                    </td>
                    <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                      {formatDuration(item.duration_seconds)}
                    </td>
                    <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                      {new Date(item.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-3">
                      <DeleteConfirmButton
                        onConfirm={() => deleteMutation.mutate(item.id)}
                        isDeleting={deleteMutation.isPending && deleteMutation.variables === item.id}
                        deleteLabel={m.app_transcribe_delete_label()}
                        confirmLabel={m.app_transcribe_delete_confirm()}
                        cancelLabel={m.app_transcribe_delete_cancel()}
                      />
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
