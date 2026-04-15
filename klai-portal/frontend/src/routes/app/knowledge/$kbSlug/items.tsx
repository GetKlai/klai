import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { List } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { DeleteConfirmButton } from '@/components/ui/delete-confirm-button'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import type { PersonalItemsResponse } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/items')({
  component: ItemsTab,
})

function ItemsTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const { data, isLoading } = useQuery<PersonalItemsResponse>({
    queryKey: ['personal-knowledge', kbSlug],
    queryFn: async () => apiFetch<PersonalItemsResponse>('/api/knowledge/personal/items', token),
    enabled: !!token,
  })

  const deleteMutation = useMutation({
    mutationFn: async (artifactId: string) => {
      setDeletingId(artifactId)
      await apiFetch(`/api/knowledge/personal/items/${artifactId}`, token, {
        method: 'DELETE',
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['personal-knowledge'] })
    },
    onSettled: () => setDeletingId(null),
  })

  if (isLoading) {
    return <p className="text-sm text-gray-400">{m.admin_connectors_loading()}</p>
  }

  if (!data?.items?.length) {
    return (
      <div className="rounded-lg border border-dashed border-gray-200 p-8 text-center">
        <List className="mx-auto h-8 w-8 text-gray-400 mb-3" />
        <p className="text-sm text-gray-400">{m.knowledge_items_empty_state()}</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-400">
              <th className="pb-2 pr-4 font-medium">{m.knowledge_items_column_title()}</th>
              <th className="pb-2 pr-4 font-medium">{m.knowledge_items_column_type()}</th>
              <th className="pb-2 pr-4 font-medium">{m.knowledge_items_column_saved_at()}</th>
              <th className="pb-2 font-medium">{m.knowledge_items_column_actions()}</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.id} className="border-b border-gray-200 last:border-0">
                <td className="py-2.5 pr-4 text-gray-900">
                  {item.path.replace(/\.md$/, '')}
                </td>
                <td className="py-2.5 pr-4">
                  {item.assertion_mode ? (
                    <Badge variant="secondary">{item.assertion_mode}</Badge>
                  ) : (
                    <span className="text-gray-400">-</span>
                  )}
                </td>
                <td className="py-2.5 pr-4 text-gray-400">
                  {new Date(item.created_at).toLocaleDateString()}
                </td>
                <td className="py-2.5">
                  <DeleteConfirmButton
                    onConfirm={() => deleteMutation.mutate(item.id)}
                    isDeleting={deletingId === item.id}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
