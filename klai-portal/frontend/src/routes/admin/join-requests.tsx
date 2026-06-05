import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { PageHeader } from '@/components/ui/page-header'
import { InlineRowButton } from '@/components/ui/inline-row-button'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '@/components/ui/data-table'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { SearchInput } from '@/components/ui/search-input'
import { Pagination } from '@/components/ui/pagination'
import { useListControls } from '@/components/ui/use-list-controls'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { adminLogger } from '@/lib/logger'

export const Route = createFileRoute('/admin/join-requests')({
  component: AdminJoinRequestsPage,
})

interface JoinRequest {
  id: number
  zitadel_user_id: string
  email: string
  display_name: string | null
  status: string
  requested_at: string
}

function AdminJoinRequestsPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-join-requests'],
    queryFn: async () => apiFetch<{ requests: JoinRequest[] }>('/api/admin/join-requests'),
    enabled: auth.isAuthenticated,
  })

  const approveMutation = useMutation({
    mutationFn: async (id: number) =>
      apiFetch(`/api/admin/join-requests/${id}/approve`, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-join-requests'] })
      adminLogger.info('Join request approved')
    },
  })

  const denyMutation = useMutation({
    mutationFn: async (id: number) =>
      apiFetch(`/api/admin/join-requests/${id}/deny`, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-join-requests'] })
      adminLogger.info('Join request denied')
    },
  })

  const requests = data?.requests ?? []
  const controls = useListControls(requests, {
    pageSize: 10,
    filter: (req, q) => {
      const needle = q.trim().toLowerCase()
      return (
        (req.display_name ?? '').toLowerCase().includes(needle) ||
        req.email.toLowerCase().includes(needle)
      )
    },
  })

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-6">
      <PageHeader
        title={m.admin_join_requests_title()}
        count={!isLoading && !error ? requests.length : undefined}
      />

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : requests.length === 0 ? (
        <ListEmptyState title={m.admin_join_requests_empty()} />
      ) : (
        <>
          {controls.showSearch && (
            <div className="max-w-sm">
              <SearchInput
                type="search"
                value={controls.query}
                onChange={(e) => controls.setQuery(e.target.value)}
                placeholder={m.admin_users_search_placeholder()}
                aria-label={m.admin_users_search_placeholder()}
              />
            </div>
          )}
          {controls.filteredCount === 0 ? (
            <ListEmptyState title={m.admin_join_requests_empty()} />
          ) : (
            <DataTable>
              <DataTableHeader>
                <DataTableRow>
                  <DataTableHead>{m.admin_join_requests_column_name()}</DataTableHead>
                  <DataTableHead>{m.admin_join_requests_column_email()}</DataTableHead>
                  <DataTableHead align="right" />
                </DataTableRow>
              </DataTableHeader>
              <DataTableBody>
                {controls.pageItems.map((req) => (
                  <DataTableRow key={req.id}>
                    <DataTableCell>{req.display_name || '-'}</DataTableCell>
                    <DataTableCell>{req.email}</DataTableCell>
                    <DataTableCell align="right">
                      <div className="flex items-center justify-end gap-1">
                        <InlineRowButton
                          tone="success"
                          onClick={() => approveMutation.mutate(req.id)}
                          disabled={approveMutation.isPending}
                        >
                          <Check /> {m.admin_join_requests_approve()}
                        </InlineRowButton>
                        <InlineRowButton
                          onClick={() => denyMutation.mutate(req.id)}
                          disabled={denyMutation.isPending}
                        >
                          <X /> {m.admin_join_requests_deny()}
                        </InlineRowButton>
                      </div>
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
          )}
          {controls.showPagination && (
            <Pagination
              page={controls.page}
              pageCount={controls.pageCount}
              onPageChange={controls.setPage}
            />
          )}
        </>
      )}
    </div>
  )
}
