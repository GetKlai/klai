import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/groups/$groupId/edit')({
  component: EditGroupPage,
})

interface Group {
  id: number
  name: string
  description: string | null
  created_at: string
  created_by: string
}

function EditGroupPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { groupId } = Route.useParams()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [duplicateError, setDuplicateError] = useState(false)
  const [initialized, setInitialized] = useState(false)

  const { data: group, isLoading } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`Failed to fetch groups (${res.status})`)
      return res.json() as Promise<{ groups: Group[] }>
    },
    enabled: !!token,
    select: (data) => data.groups.find((g) => g.id === Number(groupId)),
  })

  useEffect(() => {
    if (group && !initialized) {
      setName(group.name)
      setDescription(group.description ?? '')
      setInitialized(true)
    }
  }, [group, initialized])

  const updateMutation = useMutation({
    mutationFn: async (body: { name: string; description: string }) => {
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      })
      if (res.status === 409) throw new Error('duplicate')
      if (!res.ok) throw new Error(`Failed to update group (${res.status})`)
      return res.json() as Promise<Group>
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      toast.success(m.admin_groups_success_updated())
      void navigate({ to: '/admin/groups/$groupId', params: { groupId } })
    },
    onError: (err: Error) => {
      if (err.message === 'duplicate') {
        setDuplicateError(true)
      } else {
        toast.error(err.message)
      }
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setDuplicateError(false)
    updateMutation.mutate({ name: name.trim(), description: description.trim() })
  }

  if (isLoading) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          Loading...
        </p>
      </div>
    )
  }

  if (!group) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-destructive)]">Group not found</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_groups_edit()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/groups/$groupId', params: { groupId } })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="group-name">{m.admin_groups_name()}</Label>
              <Input
                id="group-name"
                value={name}
                onChange={(e) => { setName(e.target.value); setDuplicateError(false) }}
                placeholder={m.admin_groups_name_placeholder()}
                required
                autoFocus
              />
              {duplicateError && (
                <p className="text-sm text-[var(--color-destructive)]">
                  {m.admin_groups_error_duplicate()}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="group-description">{m.admin_groups_description()}</Label>
              <textarea
                id="group-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={m.admin_groups_description_placeholder()}
                rows={3}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-purple-deep)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
            {updateMutation.error && updateMutation.error.message !== 'duplicate' && (
              <p className="text-sm text-[var(--color-destructive)]">
                {updateMutation.error.message}
              </p>
            )}
            <div className="pt-2">
              <Button type="submit" disabled={updateMutation.isPending || !name.trim()}>
                {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {m.admin_groups_edit()}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
