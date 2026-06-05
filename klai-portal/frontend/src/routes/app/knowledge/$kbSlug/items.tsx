import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { List, Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '@/components/ui/data-table'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { BorderedRowActionIconButton, RowActionGroup } from '@/components/ui/row-action'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { useKBQuota } from '@/hooks/useKBQuota'
import type { PersonalItemsResponse } from './-kb-types'
import { kbQueryKeys } from '@/lib/kb-query-keys'

export const Route = createFileRoute('/app/knowledge/$kbSlug/items')({
  component: ItemsTab,
})

function ItemsTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const { canAddItem } = useKBQuota(kbSlug)

  const { data, isLoading } = useQuery<PersonalItemsResponse>({
    queryKey: kbQueryKeys.personalKnowledge(kbSlug),
    queryFn: async () => apiFetch<PersonalItemsResponse>('/api/knowledge/personal/items'),
    enabled: auth.isAuthenticated,
  })

  const deleteMutation = useMutation({
    mutationFn: async (artifactId: string) => {
      setDeletingId(artifactId)
      await apiFetch(`/api/knowledge/personal/items/${artifactId}`, {
        method: 'DELETE',
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.personalKnowledge(kbSlug) })
    },
    onSettled: () => {
      setDeletingId(null)
      setConfirmDeleteId(null)
    },
  })

  if (isLoading) {
    return <ListLoadingState label={m.admin_shared_loading()} />
  }

  if (!data?.items?.length) {
    return <ListEmptyState icon={List} title={m.knowledge_items_empty_state()} />
  }

  return (
    <div className="space-y-4">
      {/* Add document button - active when canAddItem, disabled with tooltip when quota reached */}
      <div className="flex items-center justify-between">
        {canAddItem ? (
          <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }}>
            <Button size="sm">
              <Plus className="h-4 w-4 mr-1.5" />
              {m.knowledge_items_add_button()}
            </Button>
          </Link>
        ) : (
          <Tooltip label={m.kb_limit_tooltip_items()}>
            <span
              className="inline-flex items-center gap-2 opacity-50 cursor-default select-none"
              aria-disabled="true"
            >
              <Button size="sm" tabIndex={-1} className="pointer-events-none">
                <Plus className="h-4 w-4 mr-1.5" />
                {m.knowledge_items_add_button()}
              </Button>
            </span>
          </Tooltip>
        )}
      </div>
      <DataTable>
        <DataTableHeader>
          <DataTableRow>
            <DataTableHead>{m.knowledge_items_column_title()}</DataTableHead>
            <DataTableHead>{m.knowledge_items_column_type()}</DataTableHead>
            <DataTableHead>{m.knowledge_items_column_saved_at()}</DataTableHead>
            <DataTableHead align="right">{m.knowledge_items_column_actions()}</DataTableHead>
          </DataTableRow>
        </DataTableHeader>
        <DataTableBody>
          {data.items.map((item) => (
            <DataTableRow key={item.id} confirming={confirmDeleteId === item.id}>
              <DataTableCell>{item.path.replace(/\.md$/, '')}</DataTableCell>
              <DataTableCell>
                {item.assertion_mode ? (
                  <Badge variant="secondary">{item.assertion_mode}</Badge>
                ) : (
                  <span className="text-gray-400">-</span>
                )}
              </DataTableCell>
              <DataTableCell className="text-gray-400">
                {new Date(item.created_at).toLocaleDateString()}
              </DataTableCell>
              <DataTableCell align="right">
                <InlineDeleteConfirm
                  isConfirming={confirmDeleteId === item.id}
                  isPending={deletingId === item.id}
                  label={m.knowledge_items_delete_confirm()}
                  cancelLabel={m.admin_users_cancel()}
                  onConfirm={() => deleteMutation.mutate(item.id)}
                  onCancel={() => setConfirmDeleteId(null)}
                >
                  <RowActionGroup>
                    <BorderedRowActionIconButton
                      label={m.knowledge_items_delete()}
                      action="delete"
                      onClick={() => setConfirmDeleteId(item.id)}
                    />
                  </RowActionGroup>
                </InlineDeleteConfirm>
              </DataTableCell>
            </DataTableRow>
          ))}
        </DataTableBody>
      </DataTable>
    </div>
  )
}
