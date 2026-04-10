import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import * as m from '@/paraglide/messages'
import { apiFetch, ApiError } from '@/lib/apiFetch'

export const Route = createFileRoute('/admin/groups/$groupId/add-member')({
  component: AddMemberPage,
})

interface Member {
  zitadel_user_id: string
  is_group_admin: boolean
  joined_at: string
}

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
}

function AddMemberPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { groupId } = Route.useParams()

  const [comboboxOpen, setComboboxOpen] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null)

  const { data: membersData } = useQuery({
    queryKey: ['admin-group-members', groupId],
    queryFn: async () => apiFetch<{ members: Member[] }>(`/api/admin/groups/${groupId}/members`, token),
    enabled: !!token,
  })

  const { data: usersData } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: OrgUser[] }>(`/api/admin/users`, token),
    enabled: !!token,
  })

  const members = membersData?.members ?? []
  const orgUsers = usersData?.users ?? []
  const memberIds = new Set(members.map((mb) => mb.zitadel_user_id))
  const availableUsers = orgUsers.filter((u) => !memberIds.has(u.zitadel_user_id))

  const selectedUser = selectedUserId
    ? orgUsers.find((u) => u.zitadel_user_id === selectedUserId)
    : null

  const addMemberMutation = useMutation({
    mutationFn: async (zitadel_user_id: string) => {
      await apiFetch(`/api/admin/groups/${groupId}/members`, token, {
        method: 'POST',
        body: JSON.stringify({ zitadel_user_id }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members', groupId] })
      toast.success(m.admin_groups_members_success_added())
      void navigate({ to: '/admin/groups/$groupId', params: { groupId } })
    },
    onError: (err: Error) => {
      if (err instanceof ApiError && err.status === 409) {
        toast.error(m.admin_groups_members_error_already_member())
      } else {
        toast.error(err.message)
      }
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (selectedUserId) {
      addMemberMutation.mutate(selectedUserId)
    }
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
          {m.admin_groups_members_add()}
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
              <Popover open={comboboxOpen} onOpenChange={setComboboxOpen}>
                <PopoverTrigger asChild>
                  <Button
                    type="button"
                    variant="outline"
                    role="combobox"
                    aria-expanded={comboboxOpen}
                    className="w-full justify-between font-normal"
                  >
                    {selectedUser
                      ? `${selectedUser.first_name} ${selectedUser.last_name}`.trim() ||
                        selectedUser.email
                      : m.admin_groups_members_search_placeholder()}
                    <span className="ml-2 opacity-50">&#x25BE;</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
                  <Command>
                    <CommandInput placeholder={m.admin_groups_members_search_placeholder()} />
                    <CommandList>
                      <CommandEmpty>No users found</CommandEmpty>
                      <CommandGroup>
                        {availableUsers.map((u) => {
                          const label = `${u.first_name} ${u.last_name}`.trim() || u.email
                          return (
                            <CommandItem
                              key={u.zitadel_user_id}
                              value={`${u.first_name} ${u.last_name} ${u.email}`}
                              onSelect={() => {
                                setSelectedUserId(u.zitadel_user_id)
                                setComboboxOpen(false)
                              }}
                            >
                              <span>{label}</span>
                              <span className="ml-auto text-xs text-[var(--color-muted-foreground)]">
                                {u.email}
                              </span>
                            </CommandItem>
                          )
                        })}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
            </div>
            <div className="pt-2">
              <Button
                type="submit"
                disabled={addMemberMutation.isPending || !selectedUserId}
              >
                {addMemberMutation.isPending && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                {m.admin_groups_members_add()}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
