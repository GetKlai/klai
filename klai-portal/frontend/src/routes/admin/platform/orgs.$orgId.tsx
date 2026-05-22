import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, Loader2, Plus, UserPlus } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import {
  usePlatformOrgDetail,
  usePlatformInvite,
  usePlatformChangeRole,
  usePlatformSuspend,
} from './-hooks'
import type { PlatformUser } from './-types'

export const Route = createFileRoute('/admin/platform/orgs/$orgId')({
  component: PlatformOrgDetailPage,
})

const ROLE_OPTIONS = [
  { value: 'personal', label: 'Personal' },
  { value: 'company', label: 'Company' },
  { value: 'kb_manager', label: 'KB manager' },
  { value: 'group_manager', label: 'Group manager' },
  { value: 'admin', label: 'Admin' },
]

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return datetime(getLocale(), iso, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const TH =
  'py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide whitespace-nowrap'
const TD = 'py-3.5 pr-4 align-top text-gray-900'

function PlatformOrgDetailPage() {
  const { orgId } = useParams({ from: '/admin/platform/orgs/$orgId' })
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = usePlatformOrgDetail(orgId)

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 space-y-8">
      <button
        type="button"
        onClick={() => void navigate({ to: '/admin/platform' })}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-900"
      >
        <ArrowLeft className="h-4 w-4" />
        Terug naar platform
      </button>

      {isLoading && (
        <p className="py-8 text-sm text-gray-400">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          Laden…
        </p>
      )}

      {error && (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      )}

      {data && (
        <>
          <div>
            <h1 className="page-title text-[26px] font-display-bold text-gray-900">
              {data.org.name}
            </h1>
            <div className="mt-2 flex items-center gap-2 flex-wrap text-sm text-gray-400">
              <span className="font-mono">{data.org.slug}</span>
              <span>·</span>
              <Badge variant="outline">{data.org.plan}</Badge>
              <Badge
                variant={
                  data.org.provisioning_status === 'complete'
                    ? 'success'
                    : 'outline'
                }
              >
                {data.org.provisioning_status}
              </Badge>
              <span>·</span>
              <span>Aangemaakt {fmtDate(data.org.created_at)}</span>
            </div>
          </div>

          {/* Subscription summary */}
          <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
            <Stat label="Gebruikers" value={data.org.user_count} />
            <Stat label="Bots" value={data.org.bot_count} />
            <Stat label="Kennisbanken" value={data.org.kb_count} />
            <Stat label="Templates" value={data.templates.length} />
            <Stat label="Seats" value={data.org.seats} />
            <Stat label="Billing" value={data.org.billing_status} />
          </div>

          {/* Users */}
          <UsersSection orgId={orgId} users={data.users} />

          {/* Bots */}
          <section>
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400 mb-3">
              Bots ({data.bots.length})
            </h2>
            {data.bots.length === 0 ? (
              <p className="text-sm text-gray-400">Geen bots.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-t border-b border-gray-200">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className={TH}>Bot</th>
                      <th className={TH}>Kennisbanken</th>
                      <th className={TH}>Aangemaakt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.bots.map((b) => (
                      <tr
                        key={b.id}
                        onClick={() =>
                          window.open(
                            `/bot/${b.widget_id}`,
                            '_blank',
                            'noopener,noreferrer',
                          )
                        }
                        className="border-b border-gray-200 last:border-b-0 cursor-pointer klai-hover"
                      >
                        <td className={TD}>
                          <span className="font-medium">{b.name}</span>
                        </td>
                        <td className={`${TD} tabular-nums`}>{b.kb_count}</td>
                        <td
                          className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}
                        >
                          {fmtDate(b.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Knowledge bases */}
          <section>
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400 mb-3">
              Kennisbanken ({data.knowledge_bases.length})
            </h2>
            {data.knowledge_bases.length === 0 ? (
              <p className="text-sm text-gray-400">Geen kennisbanken.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-t border-b border-gray-200">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className={TH}>Kennisbank</th>
                      <th className={TH}>Type</th>
                      <th className={TH}>Zichtbaarheid</th>
                      <th className={TH}>Aangemaakt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.knowledge_bases.map((kb) => (
                      <tr
                        key={kb.id}
                        className="border-b border-gray-200 last:border-b-0"
                      >
                        <td className={TD}>
                          <span className="font-medium">{kb.name}</span>
                          <p className="text-xs text-gray-400 font-mono">
                            {kb.slug}
                          </p>
                        </td>
                        <td className={TD}>
                          <Badge variant="outline">
                            {kb.owner_type === 'org'
                              ? 'Organisatie'
                              : 'Persoonlijk'}
                          </Badge>
                        </td>
                        <td className={TD}>{kb.visibility}</td>
                        <td
                          className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}
                        >
                          {fmtDate(kb.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Templates */}
          <section>
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400 mb-3">
              Templates ({data.templates.length})
            </h2>
            {data.templates.length === 0 ? (
              <p className="text-sm text-gray-400">Geen templates.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-t border-b border-gray-200">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className={TH}>Template</th>
                      <th className={TH}>Scope</th>
                      <th className={TH}>Gemaakt door</th>
                      <th className={TH}>Aangemaakt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.templates.map((t) => (
                      <tr
                        key={t.id}
                        className="border-b border-gray-200 last:border-b-0"
                      >
                        <td className={TD}>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium">{t.name}</span>
                            {!t.is_active && (
                              <Badge variant="outline">Inactief</Badge>
                            )}
                          </div>
                          <p className="text-xs text-gray-400 font-mono">
                            {t.slug}
                          </p>
                        </td>
                        <td className={TD}>
                          <Badge
                            variant={t.scope === 'org' ? 'success' : 'outline'}
                          >
                            {t.scope === 'org' ? 'Organisatie' : 'Persoonlijk'}
                          </Badge>
                        </td>
                        <td className={TD}>
                          {t.created_by_name ?? t.created_by}
                        </td>
                        <td
                          className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}
                        >
                          {fmtDate(t.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}

function UsersSection({
  orgId,
  users,
}: {
  orgId: string
  users: PlatformUser[]
}) {
  const [showInvite, setShowInvite] = useState(false)
  const changeRole = usePlatformChangeRole(orgId)
  const suspend = usePlatformSuspend(orgId)

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
          Gebruikers ({users.length})
        </h2>
        <button
          type="button"
          onClick={() => setShowInvite((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-full bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <UserPlus className="h-4 w-4" />
          Gebruiker uitnodigen
        </button>
      </div>

      {showInvite && (
        <InviteForm orgId={orgId} onClose={() => setShowInvite(false)} />
      )}

      {users.length === 0 ? (
        <p className="text-sm text-gray-400">Nog geen gebruikers.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-t border-b border-gray-200">
            <thead>
              <tr className="border-b border-gray-200">
                <th className={TH}>Gebruiker</th>
                <th className={TH}>Rol</th>
                <th className={TH}>Status</th>
                <th className={TH}>Acties</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const busy =
                  (changeRole.isPending &&
                    changeRole.variables?.zid === u.zitadel_user_id) ||
                  (suspend.isPending &&
                    suspend.variables?.zid === u.zitadel_user_id)
                return (
                  <tr
                    key={u.zitadel_user_id}
                    className="border-b border-gray-200 last:border-b-0"
                  >
                    <td className={TD}>
                      <span className="font-medium">
                        {u.display_name || u.email || u.zitadel_user_id}
                      </span>
                      {u.email && (
                        <p className="text-xs text-gray-400">{u.email}</p>
                      )}
                    </td>
                    <td className={TD}>
                      <Select
                        value={u.role}
                        disabled={busy}
                        onChange={(e) =>
                          changeRole.mutate(
                            { zid: u.zitadel_user_id, role: e.target.value },
                            {
                              onSuccess: () => toast.success('Rol bijgewerkt'),
                              onError: (err) =>
                                toast.error(
                                  err instanceof Error
                                    ? err.message
                                    : 'Mislukt',
                                ),
                            },
                          )
                        }
                        className="max-w-[10rem] text-xs"
                      >
                        {ROLE_OPTIONS.map((r) => (
                          <option key={r.value} value={r.value}>
                            {r.label}
                          </option>
                        ))}
                      </Select>
                    </td>
                    <td className={TD}>
                      <Badge
                        variant={
                          u.status === 'active' ? 'success' : 'outline'
                        }
                      >
                        {u.status}
                      </Badge>
                    </td>
                    <td className={TD}>
                      {u.status === 'suspended' ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            suspend.mutate(
                              { zid: u.zitadel_user_id, reactivate: true },
                              {
                                onSuccess: () =>
                                  toast.success('Geheractiveerd'),
                              },
                            )
                          }
                          className="text-xs font-medium text-[var(--color-success)] hover:opacity-70 disabled:opacity-40"
                        >
                          Heractiveren
                        </button>
                      ) : u.status === 'active' ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            suspend.mutate(
                              { zid: u.zitadel_user_id, reactivate: false },
                              {
                                onSuccess: () =>
                                  toast.success('Gesuspendeerd'),
                              },
                            )
                          }
                          className="text-xs font-medium text-[var(--color-destructive)] hover:opacity-70 disabled:opacity-40"
                        >
                          Suspend
                        </button>
                      ) : (
                        <span className="text-xs text-gray-400">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function InviteForm({
  orgId,
  onClose,
}: {
  orgId: string
  onClose: () => void
}) {
  const invite = usePlatformInvite(orgId)
  const [email, setEmail] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [role, setRole] = useState('personal')

  function submit(e: React.FormEvent) {
    e.preventDefault()
    invite.mutate(
      {
        email: email.trim(),
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        role,
        preferred_language: 'nl',
      },
      {
        onSuccess: () => {
          toast.success(`Uitnodiging verstuurd naar ${email.trim()}`)
          onClose()
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : 'Uitnodigen mislukt'),
      },
    )
  }

  return (
    <form
      onSubmit={submit}
      className="mb-4 rounded-xl border border-gray-200 bg-[var(--color-rl-cream)] p-4 space-y-3"
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="inv-email">E-mail</Label>
          <Input
            id="inv-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="naam@bedrijf.nl"
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="inv-role">Rol</Label>
          <Select
            id="inv-role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            {ROLE_OPTIONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="inv-first">Voornaam</Label>
          <Input
            id="inv-first"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="inv-last">Achternaam</Label>
          <Input
            id="inv-last"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            required
          />
        </div>
      </div>
      <div className="flex items-center gap-3 pt-1">
        <Button type="submit" disabled={invite.isPending}>
          {invite.isPending && (
            <Plus className="mr-2 h-4 w-4 animate-spin" />
          )}
          Uitnodiging versturen
        </Button>
        <button
          type="button"
          onClick={onClose}
          className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
        >
          Annuleren
        </button>
      </div>
    </form>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {label}
      </p>
      <p className="mt-1 text-xl font-display-bold text-gray-900 tabular-nums">
        {value}
      </p>
    </div>
  )
}
