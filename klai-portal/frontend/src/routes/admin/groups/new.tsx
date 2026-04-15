import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { apiFetch, ApiError } from '@/lib/apiFetch'

export const Route = createFileRoute('/admin/groups/new')({
  component: NewGroupPage,
})

interface Group {
  id: number
  name: string
  description: string | null
  created_at: string
  created_by: string
}

function NewGroupPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [duplicateError, setDuplicateError] = useState(false)

  const createMutation = useMutation({
    mutationFn: async (body: { name: string; description: string }) => {
      return apiFetch<Group>(`/api/admin/groups`, token, {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },
    onSuccess: (group) => {
      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      toast.success(m.admin_groups_success_created())
      void navigate({ to: '/admin/groups/$groupId', params: { groupId: String(group.id) } })
    },
    onError: (err: Error) => {
      if (err instanceof ApiError && err.status === 409) {
        setDuplicateError(true)
      } else {
        toast.error(err.message)
      }
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setDuplicateError(false)
    createMutation.mutate({ name: name.trim(), description: description.trim() })
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-xl/none font-semibold text-gray-900">
          {m.admin_groups_create()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/groups' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

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
            className="w-full rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>
        {createMutation.error && createMutation.error.message !== 'duplicate' && (
          <p className="text-sm text-[var(--color-destructive)]">
            {createMutation.error.message}
          </p>
        )}
        <div className="pt-2">
          <Button type="submit" disabled={createMutation.isPending || !name.trim()}>
            {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {m.admin_groups_create()}
          </Button>
        </div>
      </form>
    </div>
  )
}
