import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { ArrowLeft, Loader2, Trash2, Plus } from 'lucide-react'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/knowledge-bases/$kbId')({
  component: AdminKnowledgeBaseDetail,
})

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  created_at: string
  created_by: string
}

interface KbGroup {
  group_id: number
  group_name: string
  granted_at: string
}

interface Group {
  id: number
  name: string
  products: string[]
  is_system: boolean
}

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function AdminKnowledgeBaseDetail() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { kbId } = Route.useParams()
  const [selectedGroupId, setSelectedGroupId] = useState<string>('')

  // Fetch KB details
  const { data: kbData, isLoading: kbLoading } = useQuery({
    queryKey: ['admin-knowledge-bases', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/knowledge-bases`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch knowledge bases (${res.status})`)
      return res.json() as Promise<{ knowledge_bases: KnowledgeBase[] }>
    },
    enabled: !!token,
    select: (data) => data.knowledge_bases.find((kb) => kb.id === Number(kbId)),
  })

  // Fetch groups with access
  const { data: groupsData, isLoading: groupsLoading } = useQuery({
    queryKey: ['admin-kb-groups', kbId],
    queryFn: async () => {
      const res = await fetch(
        `${API_BASE}/api/admin/knowledge-bases/${kbId}/groups`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) throw new Error(`Failed to fetch KB groups (${res.status})`)
      return res.json() as Promise<{ groups: KbGroup[] }>
    },
    enabled: !!token,
  })

  // Fetch all groups for the add dropdown
  const { data: allGroupsData } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch groups (${res.status})`)
      return res.json() as Promise<{ groups: Group[] }>
    },
    enabled: !!token,
  })

  const kbGroups = groupsData?.groups ?? []
  const allGroups = allGroupsData?.groups ?? []
  const grantedGroupIds = new Set(kbGroups.map((g) => g.group_id))
  const availableGroups = allGroups.filter((g) => !grantedGroupIds.has(g.id))

  // Grant group access
  const grantMutation = useMutation({
    mutationFn: async (groupId: number) => {
      const res = await fetch(
        `${API_BASE}/api/admin/knowledge-bases/${kbId}/groups`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ group_id: groupId }),
        },
      )
      if (!res.ok) throw new Error(`Failed to grant access (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-kb-groups', kbId] })
      setSelectedGroupId('')
      toast.success(m.admin_kb_groups_grant_success())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  // Revoke group access
  const revokeMutation = useMutation({
    mutationFn: async (groupId: number) => {
      const res = await fetch(
        `${API_BASE}/api/admin/knowledge-bases/${kbId}/groups/${groupId}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        },
      )
      if (!res.ok) throw new Error(`Failed to revoke access (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-kb-groups', kbId] })
      toast.success(m.admin_kb_groups_revoke_success())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  if (kbLoading) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_kb_loading()}
        </p>
      </div>
    )
  }

  if (!kbData) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-destructive)]">Not found</p>
      </div>
    )
  }

  return (
    <div className="p-8 space-y-6 max-w-2xl">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {kbData.name}
          </h1>
          <Badge variant="secondary" className="font-mono text-xs">
            {kbData.slug}
          </Badge>
          {kbData.description && (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {kbData.description}
            </p>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/knowledge-bases' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_kb_title()}
        </Button>
      </div>

      {/* Group Access */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between mb-1">
            <h2 className="font-semibold text-lg">{m.admin_kb_groups_title()}</h2>
          </div>
          <p className="text-sm text-[var(--color-muted-foreground)] mb-4">
            {m.admin_kb_groups_subtitle()}
          </p>

          {/* Add group */}
          {availableGroups.length > 0 && (
            <div className="flex gap-2 mb-4">
              <select
                className="flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm"
                value={selectedGroupId}
                onChange={(e) => setSelectedGroupId(e.target.value)}
              >
                <option value="">{m.admin_kb_groups_add()}...</option>
                {availableGroups.map((g) => (
                  <option key={g.id} value={String(g.id)}>
                    {g.name}
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                disabled={!selectedGroupId || grantMutation.isPending}
                onClick={() => grantMutation.mutate(Number(selectedGroupId))}
              >
                {grantMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <Plus className="h-4 w-4 mr-2" />
                    {m.admin_kb_groups_add()}
                  </>
                )}
              </Button>
            </div>
          )}

          {groupsLoading ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Loading...
            </p>
          ) : kbGroups.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)] py-4 text-center">
              {m.admin_kb_groups_empty()}
            </p>
          ) : (
            <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {m.admin_kb_groups_col_group()}
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {m.admin_kb_groups_col_granted()}
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                      {/* Actions */}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {kbGroups.map((group, i) => {
                    const isRevoking =
                      revokeMutation.isPending &&
                      revokeMutation.variables === group.group_id
                    return (
                      <tr
                        key={group.group_id}
                        className={
                          i % 2 === 0
                            ? 'bg-[var(--color-card)]'
                            : 'bg-[var(--color-secondary)]'
                        }
                      >
                        <td className="px-6 py-3 text-[var(--color-purple-deep)] font-medium">
                          {group.group_name}
                        </td>
                        <td className="px-6 py-3 text-[var(--color-muted-foreground)]">
                          {formatDate(group.granted_at)}
                        </td>
                        <td className="px-6 py-3 text-right">
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                disabled={isRevoking}
                              >
                                {isRevoking ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
                                )}
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>
                                  {m.admin_kb_delete_title()}
                                </AlertDialogTitle>
                                <AlertDialogDescription>
                                  {group.group_name}
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>
                                  {m.admin_kb_delete_cancel()}
                                </AlertDialogCancel>
                                <AlertDialogAction
                                  onClick={() =>
                                    revokeMutation.mutate(group.group_id)
                                  }
                                >
                                  {m.admin_kb_delete_confirm()}
                                </AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
