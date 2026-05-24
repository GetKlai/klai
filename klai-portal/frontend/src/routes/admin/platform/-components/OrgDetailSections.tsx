import { useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { Loader2, Plus, Trash2, UserPlus } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import {
  usePlatformChangeRole,
  usePlatformDeleteUser,
  usePlatformDeprovisionTenant,
  usePlatformInvite,
  usePlatformSuspend,
} from '../-hooks'
import { PlatformMiniStat } from './PlatformShell'
import type {
  PlatformBot,
  PlatformKB,
  PlatformOrg,
  PlatformTemplate,
  PlatformUser,
} from '../-types'

const ROLE_OPTIONS = [
  { value: 'personal', label: 'Personal' },
  { value: 'company', label: 'Company' },
  { value: 'kb_manager', label: 'KB manager' },
  { value: 'group_manager', label: 'Group manager' },
  { value: 'admin', label: 'Admin' },
]

const TH =
  'py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide whitespace-nowrap'
const TD = 'py-3.5 pr-4 align-top text-gray-900'

export function OrgSummaryStats({
  org,
  templateCount,
}: {
  org: PlatformOrg
  templateCount: number
}) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
      <PlatformMiniStat label={m.platform_stat_users()} value={org.user_count} />
      <PlatformMiniStat label={m.platform_stat_bots()} value={org.bot_count} />
      <PlatformMiniStat
        label={m.platform_stat_knowledge_bases()}
        value={org.kb_count}
      />
      <PlatformMiniStat
        label={m.platform_stat_templates()}
        value={templateCount}
      />
      <PlatformMiniStat label={m.platform_col_seats()} value={org.seats} />
      <PlatformMiniStat
        label={m.platform_col_billing_status()}
        value={org.billing_status}
      />
    </div>
  )
}

export function TenantDangerZone({ org }: { org: PlatformOrg }) {
  const navigate = useNavigate()
  const deprovision = usePlatformDeprovisionTenant()
  const [open, setOpen] = useState(false)
  const [confirmText, setConfirmText] = useState('')

  return (
    <section className="rounded-xl border border-[var(--color-destructive)]/30 bg-white p-5">
      <h2 className="mb-1 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--color-destructive)]">
        {m.platform_danger_zone()}
      </h2>
      <p className="text-sm text-gray-500">{m.platform_danger_description()}</p>

      {!open ? (
        <Button
          type="button"
          variant="outline"
          onClick={() => setOpen(true)}
          className="mt-4 border-[var(--color-destructive)] text-[var(--color-destructive)] hover:opacity-70"
        >
          <Trash2 className="h-4 w-4" />
          {m.platform_delete_tenant()}
        </Button>
      ) : (
        <div className="mt-4 space-y-2">
          <Label htmlFor="confirm-slug">
            {m.platform_confirm_slug_prefix()}{' '}
            <span className="font-mono text-gray-900">{org.slug}</span>{' '}
            {m.platform_confirm_slug_suffix()}
          </Label>
          <div className="flex items-center gap-3">
            <Input
              id="confirm-slug"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={org.slug}
              className="max-w-xs"
              autoComplete="off"
            />
            <Button
              variant="destructive"
              disabled={confirmText !== org.slug || deprovision.isPending}
              onClick={() =>
                deprovision.mutate(org.slug, {
                  onSuccess: () => {
                    toast.success(m.platform_delete_tenant_started())
                    void navigate({ to: '/admin/platform' })
                  },
                  onError: (err) =>
                    toast.error(
                      err instanceof Error
                        ? err.message
                        : m.platform_delete_failed(),
                    ),
                })
              }
            >
              {deprovision.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {m.platform_delete_permanently()}
            </Button>
            <Button
              type="button"
              variant="link"
              onClick={() => {
                setOpen(false)
                setConfirmText('')
              }}
              className="h-auto p-0 text-sm text-gray-400 no-underline hover:text-gray-900 hover:no-underline"
            >
              {m.admin_users_cancel()}
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

export function UsersSection({
  orgId,
  users,
}: {
  orgId: string
  users: PlatformUser[]
}) {
  const [showInvite, setShowInvite] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const changeRole = usePlatformChangeRole(orgId)
  const suspend = usePlatformSuspend(orgId)
  const del = usePlatformDeleteUser(orgId)

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
          {m.platform_section_users({ count: users.length })}
        </h2>
        <Button
          type="button"
          size="sm"
          onClick={() => setShowInvite((v) => !v)}
        >
          <UserPlus className="h-4 w-4" />
          {m.platform_invite_user()}
        </Button>
      </div>

      {showInvite && (
        <InviteForm orgId={orgId} onClose={() => setShowInvite(false)} />
      )}

      {users.length === 0 ? (
        <p className="text-sm text-gray-400">{m.platform_no_users()}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-b border-t border-gray-200 text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className={TH}>{m.platform_col_user()}</th>
                <th className={TH}>{m.platform_col_role()}</th>
                <th className={TH}>{m.platform_col_status()}</th>
                <th className={TH}>{m.platform_col_actions()}</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const busy =
                  (changeRole.isPending &&
                    changeRole.variables?.zid === u.zitadel_user_id) ||
                  (suspend.isPending &&
                    suspend.variables?.zid === u.zitadel_user_id) ||
                  (del.isPending && del.variables === u.zitadel_user_id)
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
                              onSuccess: () =>
                                toast.success(m.platform_role_updated()),
                              onError: (err) =>
                                toast.error(
                                  err instanceof Error
                                    ? err.message
                                    : m.admin_shared_error_generic(),
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
                        variant={u.status === 'active' ? 'success' : 'outline'}
                      >
                        {u.status}
                      </Badge>
                    </td>
                    <td className={TD}>
                      <div className="flex items-center gap-3">
                        {u.status === 'suspended' ? (
                          <Button
                            type="button"
                            variant="link"
                            disabled={busy}
                            onClick={() =>
                              suspend.mutate(
                                { zid: u.zitadel_user_id, reactivate: true },
                                {
                                  onSuccess: () =>
                                    toast.success(m.platform_user_reactivated()),
                                },
                              )
                            }
                            className="h-auto p-0 text-xs font-medium text-[var(--color-success)] no-underline hover:opacity-70 hover:no-underline disabled:opacity-40"
                          >
                            {m.platform_reactivate()}
                          </Button>
                        ) : u.status === 'active' ? (
                          <Button
                            type="button"
                            variant="link"
                            disabled={busy}
                            onClick={() =>
                              suspend.mutate(
                                { zid: u.zitadel_user_id, reactivate: false },
                                {
                                  onSuccess: () =>
                                    toast.success(m.platform_user_suspended()),
                                },
                              )
                            }
                            className="h-auto p-0 text-xs font-medium text-[var(--color-destructive)] no-underline hover:opacity-70 hover:no-underline disabled:opacity-40"
                          >
                            {m.platform_suspend()}
                          </Button>
                        ) : null}

                        {confirmDelete === u.zitadel_user_id ? (
                          <span className="inline-flex items-center gap-2 whitespace-nowrap text-xs">
                            <span className="text-gray-500">
                              {m.platform_confirm_short()}
                            </span>
                            <Button
                              type="button"
                              variant="link"
                              disabled={busy}
                              onClick={() =>
                                del.mutate(u.zitadel_user_id, {
                                  onSuccess: () => {
                                    toast.success(m.platform_user_deleted())
                                    setConfirmDelete(null)
                                  },
                                  onError: (err) => {
                                    toast.error(
                                      err instanceof Error
                                        ? err.message
                                        : m.platform_delete_failed(),
                                    )
                                    setConfirmDelete(null)
                                  },
                                })
                              }
                              className="h-auto p-0 text-xs font-medium text-[var(--color-destructive)] no-underline hover:opacity-70 hover:no-underline disabled:opacity-40"
                            >
                              {busy ? m.platform_busy() : m.platform_yes_delete()}
                            </Button>
                            <Button
                              type="button"
                              variant="link"
                              onClick={() => setConfirmDelete(null)}
                              className="h-auto p-0 text-xs text-gray-400 no-underline hover:text-gray-900 hover:no-underline"
                            >
                              {m.platform_no()}
                            </Button>
                          </span>
                        ) : (
                          <Button
                            type="button"
                            variant="link"
                            disabled={busy}
                            onClick={() => setConfirmDelete(u.zitadel_user_id)}
                            className="h-auto p-0 text-xs font-medium text-[var(--color-destructive)] no-underline hover:opacity-70 hover:no-underline disabled:opacity-40"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            {m.platform_delete()}
                          </Button>
                        )}
                      </div>
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

export function BotsSection({
  bots,
  fmtDate,
}: {
  bots: PlatformBot[]
  fmtDate: (s: string | null) => string
}) {
  return (
    <section>
      <h2 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {m.platform_section_bots({ count: bots.length })}
      </h2>
      {bots.length === 0 ? (
        <p className="text-sm text-gray-400">{m.platform_no_bots()}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-b border-t border-gray-200 text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className={TH}>{m.platform_col_bot()}</th>
                <th className={TH}>{m.platform_col_knowledge_bases()}</th>
                <th className={TH}>{m.platform_col_created()}</th>
              </tr>
            </thead>
            <tbody>
              {bots.map((b) => (
                <tr
                  key={b.id}
                  onClick={() =>
                    window.open(
                      `/bot/${b.widget_id}`,
                      '_blank',
                      'noopener,noreferrer',
                    )
                  }
                  className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
                >
                  <td className={TD}>
                    <span className="font-medium">{b.name}</span>
                  </td>
                  <td className={`${TD} tabular-nums`}>{b.kb_count}</td>
                  <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
                    {fmtDate(b.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function KnowledgeBasesSection({
  knowledgeBases,
  fmtDate,
}: {
  knowledgeBases: PlatformKB[]
  fmtDate: (s: string | null) => string
}) {
  return (
    <section>
      <h2 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {m.platform_section_knowledge_bases({
          count: knowledgeBases.length,
        })}
      </h2>
      {knowledgeBases.length === 0 ? (
        <p className="text-sm text-gray-400">
          {m.platform_no_knowledge_bases()}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-b border-t border-gray-200 text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className={TH}>{m.platform_col_knowledge_base()}</th>
                <th className={TH}>{m.platform_col_type()}</th>
                <th className={TH}>{m.platform_col_visibility()}</th>
                <th className={TH}>{m.platform_col_created()}</th>
              </tr>
            </thead>
            <tbody>
              {knowledgeBases.map((kb) => (
                <tr
                  key={kb.id}
                  className="border-b border-gray-200 last:border-b-0"
                >
                  <td className={TD}>
                    <span className="font-medium">{kb.name}</span>
                    <p className="font-mono text-xs text-gray-400">{kb.slug}</p>
                  </td>
                  <td className={TD}>
                    <Badge variant="outline">
                      {kb.owner_type === 'org'
                        ? m.platform_scope_organization()
                        : m.platform_scope_personal()}
                    </Badge>
                  </td>
                  <td className={TD}>{kb.visibility}</td>
                  <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
                    {fmtDate(kb.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function TemplatesSection({
  templates,
  fmtDate,
}: {
  templates: PlatformTemplate[]
  fmtDate: (s: string | null) => string
}) {
  return (
    <section>
      <h2 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {m.platform_section_templates({ count: templates.length })}
      </h2>
      {templates.length === 0 ? (
        <p className="text-sm text-gray-400">{m.platform_no_templates()}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-b border-t border-gray-200 text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className={TH}>{m.platform_col_template()}</th>
                <th className={TH}>{m.platform_col_scope()}</th>
                <th className={TH}>{m.platform_col_created_by()}</th>
                <th className={TH}>{m.platform_col_created()}</th>
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => (
                <tr
                  key={t.id}
                  className="border-b border-gray-200 last:border-b-0"
                >
                  <td className={TD}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{t.name}</span>
                      {!t.is_active && (
                        <Badge variant="outline">{m.platform_inactive()}</Badge>
                      )}
                    </div>
                    <p className="font-mono text-xs text-gray-400">{t.slug}</p>
                  </td>
                  <td className={TD}>
                    <Badge variant={t.scope === 'org' ? 'success' : 'outline'}>
                      {t.scope === 'org'
                        ? m.platform_scope_organization()
                        : m.platform_scope_personal()}
                    </Badge>
                  </td>
                  <td className={TD}>{t.created_by_name ?? t.created_by}</td>
                  <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
                    {fmtDate(t.created_at)}
                  </td>
                </tr>
              ))}
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

  function submit(e: FormEvent) {
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
          toast.success(m.platform_invite_sent({ email: email.trim() }))
          onClose()
        },
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : m.platform_invite_failed(),
          ),
      },
    )
  }

  return (
    <form
      onSubmit={submit}
      className="mb-4 space-y-3 rounded-xl border border-gray-200 bg-[var(--color-rl-cream)] p-4"
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="inv-email">{m.platform_email()}</Label>
          <Input
            id="inv-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={m.platform_email_placeholder()}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="inv-role">{m.platform_role()}</Label>
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
          <Label htmlFor="inv-first">{m.platform_first_name()}</Label>
          <Input
            id="inv-first"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="inv-last">{m.platform_last_name()}</Label>
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
          {m.platform_send_invite()}
        </Button>
        <Button
          type="button"
          variant="link"
          onClick={onClose}
          className="h-auto p-0 text-sm text-gray-400 no-underline hover:text-gray-900 hover:no-underline"
        >
          {m.admin_users_cancel()}
        </Button>
      </div>
    </form>
  )
}
