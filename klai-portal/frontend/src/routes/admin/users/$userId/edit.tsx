import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
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
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { useSuspendUser, useReactivateUser, useOffboardUser } from '@/hooks/useUserLifecycle'
import { OffboardWizard } from '@/components/admin/offboard-wizard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'
import { ProfilePicker } from '../../_components/ProfilePicker'
import { cleanErrorMessage } from '../../_components/errors'

export const Route = createFileRoute('/admin/users/$userId/edit')({
  component: EditUserPage,
})

type Language = 'nl' | 'en'
type UserStatus = 'active' | 'suspended' | 'offboarded'

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  preferred_language: Language
  status: UserStatus
  invite_pending: boolean
  role: ProfileRole
}

function EditUserPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { userId } = Route.useParams()
  const { user: currentUser } = useCurrentUser()
  const isSelf = userId === currentUser?.user_id

  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [language, setLanguage] = useState<Language>('nl')
  const [selectedProfile, setSelectedProfile] = useState<ProfileRole | ''>('')
  const [originalProfile, setOriginalProfile] = useState<ProfileRole | ''>('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const { data: usersData } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: User[] }>(`/api/admin/users`),
    enabled: auth.isAuthenticated,
  })

  const user = usersData?.users.find((u) => u.zitadel_user_id === userId)

  useEffect(() => {
    if (user) {
      setFirstName(user.first_name)
      setLastName(user.last_name)
      setLanguage(user.preferred_language)
      if (user.role && PROFILE_LADDER.includes(user.role)) {
        setSelectedProfile(user.role)
        setOriginalProfile(user.role)
      }
    }
  }, [user])

  const suspendMutation = useSuspendUser()
  const reactivateMutation = useReactivateUser()
  const offboardMutation = useOffboardUser()
  const [offboardWizardOpen, setOffboardWizardOpen] = useState(false)

  // SPEC-PORTAL-ADMIN-UI-001 v0.3.0 REQ-12: ÉÉN form, ÉÉN save. Submit-handler
  // stuurt PATCH /users/<id> voor naam/taal en - alleen als profile gewijzigd
  // is - PATCH /users/<id>/role. Sequentieel zodat de role-update niet stilletjes
  // overgeslagen wordt als de basis-update een 400/422 oplevert.
  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return
    setSaving(true)
    setSaveError(null)
    try {
      await apiFetch(`/api/admin/users/${userId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          first_name: firstName,
          last_name: lastName,
          preferred_language: language,
        }),
      })

      const profileChanged = selectedProfile && selectedProfile !== originalProfile
      if (profileChanged && !isSelf) {
        await apiFetch(`/api/admin/users/${userId}/role`, {
          method: 'PATCH',
          body: JSON.stringify({ role: selectedProfile }),
        })
      }

      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      toast.success(m.admin_settings_saved())
      void navigate({ to: '/admin/users' })
    } catch (err) {
      setSaveError(cleanErrorMessage(err, m.admin_users_error_edit_generic()))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto max-w-lg px-6 pt-4 pb-10 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_users_edit_heading()}
          </h1>
          <p className="text-sm text-gray-400">
            {m.admin_users_edit_subtitle()}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void navigate({ to: '/admin/users' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <form onSubmit={(e) => void handleSave(e)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="first-name">{m.admin_users_field_first_name()}</Label>
            <Input
              id="first-name"
              type="text"
              required
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="last-name">{m.admin_users_field_last_name()}</Label>
            <Input
              id="last-name"
              type="text"
              required
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="language">{m.admin_users_field_language()}</Label>
          <Select
            id="language"
            value={language}
            onChange={(e) => setLanguage(e.target.value as Language)}
          >
            <option value="nl">{m.admin_users_language_nl()}</option>
            <option value="en">{m.admin_users_language_en()}</option>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label>{m.admin_users_field_profile()}</Label>
          <ProfilePicker
            value={selectedProfile}
            onChange={setSelectedProfile}
            disabled={isSelf}
            disabledMessage={m.profile_picker_self_edit_hint()}
          />
        </div>

        {saveError && (
          <p className="text-sm text-[var(--color-destructive)]">{saveError}</p>
        )}

        <div className="pt-2 flex items-center gap-3">
          <Button type="submit" disabled={saving || !user}>
            {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {saving ? m.admin_users_edit_submit_loading() : m.admin_users_edit_submit()}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => void navigate({ to: '/admin/users' })}
          >
            {m.admin_users_cancel()}
          </Button>
        </div>
      </form>

      {/* Lifecycle actions - destructive, separate from save */}
      {user && (user.status === 'suspended' || (user.status === 'active' && !user.invite_pending)) && (
        <div className="border-t pt-6 flex flex-wrap gap-3">
          {user.status === 'active' && !user.invite_pending && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" disabled={suspendMutation.isPending}>
                  {suspendMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {m.admin_users_action_suspend()}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{m.admin_users_confirm_suspend_title()}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {m.admin_users_confirm_suspend_description()}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
                  <AlertDialogAction onClick={() => suspendMutation.mutate(userId)}>
                    {m.admin_users_action_suspend()}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}

          {user.status === 'suspended' && (
            <Button
              variant="outline"
              disabled={reactivateMutation.isPending}
              onClick={() => reactivateMutation.mutate(userId)}
            >
              {reactivateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {m.admin_users_action_reactivate()}
            </Button>
          )}

          {(user.status === 'active' || user.status === 'suspended') && !user.invite_pending && (
            <>
              <Button
                variant="destructive"
                disabled={offboardMutation.isPending}
                onClick={() => setOffboardWizardOpen(true)}
              >
                {offboardMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {m.admin_users_action_offboard()}
              </Button>
              {currentUser?.user_id && (
                <OffboardWizard
                  userId={userId}
                  userLabel={`${user.first_name} ${user.last_name}`.trim() || user.email}
                  currentAdminId={currentUser.user_id}
                  open={offboardWizardOpen}
                  onOpenChange={setOffboardWizardOpen}
                />
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
