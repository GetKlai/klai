import { createFileRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
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
import { apiFetch } from '@/lib/apiFetch'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'
import { cleanErrorMessage } from '../../_components/errors'

export const Route = createFileRoute('/admin/profiles/$profile/add-member')({
  component: AddProfileMemberPage,
  beforeLoad: ({ params }) => {
    if (!PROFILE_LADDER.includes(params.profile as ProfileRole)) {
      throw redirect({ to: '/admin/profiles' })
    }
  },
})

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  role: ProfileRole
}

function AddProfileMemberPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { profile } = Route.useParams()
  const profileRole = profile as ProfileRole

  const [comboboxOpen, setComboboxOpen] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null)

  const msgs = m as unknown as Record<string, (() => string) | undefined>
  const labelFn = msgs[`profile_${profileRole}_label`]
  const profileLabel = labelFn ? labelFn() : profileRole

  const { data: usersData } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: OrgUser[] }>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })

  const orgUsers = usersData?.users ?? []
  // Only show users whose current role is NOT the target profile.
  const availableUsers = orgUsers.filter((u) => u.role !== profileRole)

  const selectedUser = selectedUserId
    ? orgUsers.find((u) => u.zitadel_user_id === selectedUserId)
    : null

  const addMemberMutation = useMutation({
    mutationFn: async (zitadel_user_id: string) => {
      await apiFetch(`/api/admin/users/${zitadel_user_id}/role`, {
        method: 'PATCH',
        body: JSON.stringify({ role: profileRole }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast.success(m.admin_groups_members_success_added())
      void navigate({ to: '/admin/profiles/$profile', params: { profile: profileRole } })
    },
    onError: (err: Error) => {
      toast.error(cleanErrorMessage(err, m.admin_profiles_error_change()))
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (selectedUserId) {
      addMemberMutation.mutate(selectedUserId)
    }
  }

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_groups_members_add()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() =>
            navigate({ to: '/admin/profiles/$profile', params: { profile: profileRole } })
          }
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <p className="mb-4 text-sm text-gray-400">
            {profileLabel}
          </p>
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
                              <span className="ml-auto text-xs text-gray-400">
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
