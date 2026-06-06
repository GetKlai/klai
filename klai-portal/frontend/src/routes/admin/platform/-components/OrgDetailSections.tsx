import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState, type FormEvent } from 'react'
import { Loader2, Plus, Trash2, UserPlus } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ListEmptyState } from '@/components/ui/list-state'
import {
  BorderedRowActionIconButton,
  RowActionGroup,
} from '@/components/ui/row-action'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import {
  usePlatformChangeRole,
  usePlatformDeleteUser,
  usePlatformDeprovisionTenant,
  usePlatformInvite,
  usePlatformUnlocks,
  usePlatformUpdateUnlocks,
  usePlatformRetryDeleteUser,
  usePlatformSuspend,
} from '../-hooks'
import { PlatformMessageComposer } from './PlatformMessageComposer'
import { StatCard } from '@/components/ui/stat-card'
import { extensionDescription, extensionLabel } from '@/lib/extensions-i18n'
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


export function OrgSummaryStats({
  org,
  templateCount,
}: {
  org: PlatformOrg
  templateCount: number
}) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
      <StatCard size="sm" label={m.platform_stat_users()} value={org.user_count} />
      <StatCard size="sm" label={m.platform_stat_bots()} value={org.bot_count} />
      <StatCard size="sm"
        label={m.platform_stat_knowledge_bases()}
        value={org.kb_count}
      />
      <StatCard size="sm"
        label={m.platform_stat_templates()}
        value={templateCount}
      />
      <StatCard size="sm" label={m.platform_col_seats()} value={org.seats} />
      <StatCard size="sm"
        label={m.platform_col_billing_status()}
        value={org.billing_status}
      />
    </div>
  )
}

export function TenantFeaturesSection({
  orgId,
  org,
}: {
  orgId: string
  org: PlatformOrg
}) {
  const unlocks = usePlatformUnlocks(org.slug)
  const updateUnlocks = usePlatformUpdateUnlocks(orgId, org.slug)
  const [stagedFeatures, setStagedFeatures] = useState<Set<string>>(
    () => new Set(org.platform_unlocked_features ?? []),
  )
  const [savedFeatures, setSavedFeatures] = useState(false)

  useEffect(() => {
    if (unlocks.data) {
      setStagedFeatures(new Set(unlocks.data.platform_unlocked_features))
    }
  }, [unlocks.data])

  const saved = new Set(
    unlocks.data?.platform_unlocked_features ?? org.platform_unlocked_features ?? [],
  )
  const dirty =
    stagedFeatures.size !== saved.size ||
    [...stagedFeatures].some((key) => !saved.has(key))

  function stageFeature(key: string, enabled: boolean) {
    setStagedFeatures((prev) => {
      const next = new Set(prev)
      if (enabled) next.add(key)
      else next.delete(key)
      return next
    })
  }

  function saveFeatures() {
    updateUnlocks.mutate([...stagedFeatures].sort(), {
      onSuccess: () => {
        setSavedFeatures(true)
        setTimeout(() => setSavedFeatures(false), 2500)
        toast.success(m.admin_settings_saved())
      },
      onError: (err) =>
        toast.error(
          err instanceof Error ? err.message : m.admin_settings_error_save(),
        ),
    })
  }

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
            {m.admin_settings_extensions_title()}
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            {m.admin_settings_extensions_description_platform()}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          onClick={saveFeatures}
          disabled={
            updateUnlocks.isPending ||
            unlocks.isLoading ||
            savedFeatures ||
            !dirty
          }
        >
          {updateUnlocks.isPending && (
            <Loader2 className="h-4 w-4 animate-spin" />
          )}
          {savedFeatures
            ? m.admin_settings_saved()
            : updateUnlocks.isPending
              ? m.admin_settings_saving()
              : m.admin_settings_save()}
        </Button>
      </div>

      {unlocks.isLoading ? (
        <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
      ) : unlocks.error ? (
        <p className="text-sm text-[var(--color-destructive)]">
          {m.admin_settings_error_fetch()}
        </p>
      ) : unlocks.data?.features.length ? (
        <ul className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {unlocks.data.features.map((feature) => {
            const staged = stagedFeatures.has(feature.key)
            return (
              <li
                key={feature.key}
                className="flex items-center justify-between gap-4 px-2 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] font-display text-gray-900">
                    {extensionLabel(feature.key)}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {extensionDescription(feature.key)}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <Badge variant={staged ? 'success' : 'outline'}>
                    {staged
                      ? m.admin_settings_extensions_status_on()
                      : m.admin_settings_extensions_status_off()}
                  </Badge>
                  <Checkbox
                    checked={staged}
                    onChange={(e) => stageFeature(feature.key, e.target.checked)}
                    disabled={updateUnlocks.isPending}
                    label=""
                  />
                </div>
              </li>
            )
          })}
        </ul>
      ) : (
        <ListEmptyState title={m.platform_tenant_features_empty()} />
      )}
    </section>
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
  const retryDelete = usePlatformRetryDeleteUser(orgId)

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
        <ListEmptyState title={m.platform_no_users()} />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.platform_col_user()}</DataTableHead>
              <DataTableHead>{m.platform_col_role()}</DataTableHead>
              <DataTableHead>{m.platform_col_status()}</DataTableHead>
              <DataTableHead align="right">
                {m.platform_col_actions()}
              </DataTableHead>
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {users.map((u) => {
              const busy =
                (changeRole.isPending &&
                  changeRole.variables?.zid === u.zitadel_user_id) ||
                (suspend.isPending &&
                  suspend.variables?.zid === u.zitadel_user_id) ||
                (del.isPending && del.variables === u.zitadel_user_id) ||
                (retryDelete.isPending &&
                  retryDelete.variables === u.zitadel_user_id)
              const deleteFailed = u.deletion_status === 'failed_partial'
              const failureStep = u.deletion_last_attempted_step
              const isConfirmingDelete = confirmDelete === u.zitadel_user_id
              return (
                <DataTableRow
                  key={u.zitadel_user_id}
                  confirming={isConfirmingDelete}
                >
                  <DataTableCell>
                    <span className="font-medium">
                      {u.display_name || u.email || u.zitadel_user_id}
                    </span>
                    {u.email && (
                      <p className="text-xs text-gray-400">{u.email}</p>
                    )}
                  </DataTableCell>
                  <DataTableCell>
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
                      className="text-xs"
                      containerClassName="max-w-[10rem]"
                    >
                      {ROLE_OPTIONS.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </Select>
                  </DataTableCell>
                  <DataTableCell>
                    <Badge
                      variant={
                        deleteFailed
                          ? 'outline'
                          : u.status === 'active'
                            ? 'success'
                            : 'outline'
                      }
                    >
                      {deleteFailed
                        ? m.platform_user_delete_failed_status()
                        : u.status}
                    </Badge>
                    {deleteFailed && failureStep ? (
                      <p className="mt-1 text-xs text-[var(--color-destructive)]">
                        {failureStep}
                      </p>
                    ) : null}
                  </DataTableCell>
                  <DataTableCell align="right">
                    <div className="flex items-center justify-end gap-1">
                      <PlatformMessageComposer user={u} />
                      <InlineDeleteConfirm
                        isConfirming={isConfirmingDelete}
                        isPending={busy}
                        label={busy ? m.platform_busy() : m.platform_yes_delete()}
                        cancelLabel={m.platform_no()}
                        onConfirm={() =>
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
                        onCancel={() => setConfirmDelete(null)}
                      >
                        <RowActionGroup>
                          {u.status === 'suspended' ? (
                            <BorderedRowActionIconButton
                              label={m.platform_reactivate()}
                              action="reactivate"
                              disabled={busy}
                              spinner={
                                suspend.isPending &&
                                suspend.variables?.zid === u.zitadel_user_id
                                  ? <Loader2 className="animate-spin" />
                                  : undefined
                              }
                              onClick={() =>
                                suspend.mutate(
                                  { zid: u.zitadel_user_id, reactivate: true },
                                  {
                                    onSuccess: () =>
                                      toast.success(m.platform_user_reactivated()),
                                  },
                                )
                              }
                            />
                          ) : u.status === 'active' ? (
                            <BorderedRowActionIconButton
                              label={m.platform_suspend()}
                              action="suspend"
                              disabled={busy}
                              spinner={
                                suspend.isPending &&
                                suspend.variables?.zid === u.zitadel_user_id
                                  ? <Loader2 className="animate-spin" />
                                  : undefined
                              }
                              onClick={() =>
                                suspend.mutate(
                                  { zid: u.zitadel_user_id, reactivate: false },
                                  {
                                    onSuccess: () =>
                                      toast.success(m.platform_user_suspended()),
                                  },
                                )
                              }
                            />
                          ) : null}

                          {deleteFailed ? (
                            <BorderedRowActionIconButton
                              label={m.platform_retry_delete()}
                              action="retry"
                              disabled={busy}
                              spinner={
                                retryDelete.isPending &&
                                retryDelete.variables === u.zitadel_user_id
                                  ? <Loader2 className="animate-spin" />
                                  : undefined
                              }
                              onClick={() =>
                                retryDelete.mutate(u.zitadel_user_id, {
                                  onSuccess: () =>
                                    toast.success(m.platform_user_deleted()),
                                  onError: (err) =>
                                    toast.error(
                                      err instanceof Error
                                        ? err.message
                                        : m.platform_delete_failed(),
                                    ),
                                })
                              }
                            />
                          ) : null}
                          <BorderedRowActionIconButton
                            label={m.platform_delete()}
                            action="delete"
                            disabled={busy}
                            onClick={() => setConfirmDelete(u.zitadel_user_id)}
                          />
                        </RowActionGroup>
                      </InlineDeleteConfirm>
                    </div>
                  </DataTableCell>
                </DataTableRow>
              )
            })}
          </DataTableBody>
        </DataTable>
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
        <ListEmptyState title={m.platform_no_bots()} />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.platform_col_bot()}</DataTableHead>
              <DataTableHead>{m.platform_col_knowledge_bases()}</DataTableHead>
              <DataTableHead>{m.platform_col_created()}</DataTableHead>
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {bots.map((b) => (
              <DataTableRow
                key={b.id}
                interactive
                onClick={() =>
                  window.open(
                    `/bot/${b.widget_id}`,
                    '_blank',
                    'noopener,noreferrer',
                  )
                }
              >
                <DataTableCell>
                  <span className="font-medium">{b.name}</span>
                </DataTableCell>
                <DataTableCell className="tabular-nums">{b.kb_count}</DataTableCell>
                <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
                  {fmtDate(b.created_at)}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
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
        <ListEmptyState title={m.platform_no_knowledge_bases()} />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.platform_col_knowledge_base()}</DataTableHead>
              <DataTableHead>{m.platform_col_type()}</DataTableHead>
              <DataTableHead>{m.platform_col_visibility()}</DataTableHead>
              <DataTableHead>{m.platform_col_created()}</DataTableHead>
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {knowledgeBases.map((kb) => (
              <DataTableRow key={kb.id}>
                <DataTableCell>
                  <span className="font-medium">{kb.name}</span>
                  <p className="font-mono text-xs text-gray-400">{kb.slug}</p>
                </DataTableCell>
                <DataTableCell>
                  <Badge variant="outline">
                    {kb.owner_type === 'org'
                      ? m.platform_scope_organization()
                      : m.platform_scope_personal()}
                  </Badge>
                </DataTableCell>
                <DataTableCell>{kb.visibility}</DataTableCell>
                <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
                  {fmtDate(kb.created_at)}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
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
        <ListEmptyState title={m.platform_no_templates()} />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.platform_col_template()}</DataTableHead>
              <DataTableHead>{m.platform_col_scope()}</DataTableHead>
              <DataTableHead>{m.platform_col_created_by()}</DataTableHead>
              <DataTableHead>{m.platform_col_created()}</DataTableHead>
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {templates.map((t) => (
              <DataTableRow key={t.id}>
                <DataTableCell>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{t.name}</span>
                    {!t.is_active && (
                      <Badge variant="outline">{m.platform_inactive()}</Badge>
                    )}
                  </div>
                  <p className="font-mono text-xs text-gray-400">{t.slug}</p>
                </DataTableCell>
                <DataTableCell>
                  <Badge variant={t.scope === 'org' ? 'success' : 'outline'}>
                    {t.scope === 'org'
                      ? m.platform_scope_organization()
                      : m.platform_scope_personal()}
                  </Badge>
                </DataTableCell>
                <DataTableCell>{t.created_by_name ?? t.created_by}</DataTableCell>
                <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
                  {fmtDate(t.created_at)}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
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
