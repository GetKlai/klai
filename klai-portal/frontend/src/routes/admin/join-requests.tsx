import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Check, X } from 'lucide-react'
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

  const { data, isLoading } = useQuery({
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{m.admin_join_requests_title()}</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{m.admin_join_requests_title()}</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">...</p>
          ) : requests.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_join_requests_empty()}</p>
          ) : (
            <table className="w-full text-sm border-t border-b border-[var(--color-border)]">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                    Name
                  </th>
                  <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                    Email
                  </th>
                  <th className="py-3 text-right w-36" />
                </tr>
              </thead>
              <tbody>
                {requests.map((req) => (
                  <tr key={req.id} className="border-b border-[var(--color-border)] last:border-b-0">
                    <td className="py-4 pr-4 align-top">{req.display_name || '-'}</td>
                    <td className="py-4 pr-4 align-top">{req.email}</td>
                    <td className="py-4 align-top text-right w-36">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="sm"
                          className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5 bg-[var(--color-success)] text-white hover:opacity-70"
                          onClick={() => approveMutation.mutate(req.id)}
                          disabled={approveMutation.isPending}
                        >
                          <Check /> {m.admin_join_requests_approve()}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 text-[10px] px-2 gap-1 [&_svg]:size-2.5"
                          onClick={() => denyMutation.mutate(req.id)}
                          disabled={denyMutation.isPending}
                        >
                          <X /> {m.admin_join_requests_deny()}
                        </Button>
                      </div>
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
