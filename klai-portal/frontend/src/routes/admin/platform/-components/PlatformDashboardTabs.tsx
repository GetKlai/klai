import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import {
  Activity,
  ArchiveX,
  Bug,
  CheckCircle2,
  ExternalLink,
  LifeBuoy,
  Link2,
  Loader2,
  PlusCircle,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import * as m from '@/paraglide/messages'
import {
  usePlatformBots,
  usePlatformChatErrors,
  usePlatformFeedbackCreateItem,
  usePlatformFeedbackDismiss,
  usePlatformFeedbackItems,
  usePlatformFeedbackLinkItem,
  usePlatformFeedbackSubmissions,
  usePlatformFeedbackSupport,
  usePlatformKnowledgeBases,
  usePlatformOrgs,
  usePlatformTemplates,
  usePlatformUsers,
  usePortalHealth,
} from '../-hooks'
import type { PlatformFeedbackSubmission } from '../-types'
import { PlatformTableShell } from './PlatformShell'

const TH =
  'py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide whitespace-nowrap'
const TD = 'py-3.5 pr-4 align-top text-gray-900'

export function StatusTab() {
  const health = usePortalHealth()
  const portalUp = health.isSuccess && health.data.status === 'ok'

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-gray-200 bg-white px-5 py-5">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Activity className="h-5 w-5 shrink-0 text-gray-400" />
            <div>
              <p className="text-[15px] font-display text-gray-900">
                {m.platform_status_portal_api()}
              </p>
              <p className="text-sm text-gray-400">
                {m.platform_status_portal_api_description()}
              </p>
            </div>
          </div>
          {health.isLoading ? (
            <span className="inline-flex items-center gap-1.5 text-sm text-gray-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              {m.platform_checking()}
            </span>
          ) : portalUp ? (
            <Badge variant="success">{m.platform_operational()}</Badge>
          ) : (
            <Badge variant="destructive">{m.platform_unreachable()}</Badge>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white px-5 py-5">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Bug className="h-5 w-5 shrink-0 text-gray-400" />
            <div>
              <p className="text-[15px] font-display text-gray-900">
                {m.platform_user_errors()}
              </p>
              <p className="mt-0.5 text-sm text-gray-400">
                {m.platform_user_errors_description()}
              </p>
            </div>
          </div>
          <a
            href="https://errors.getklai.com"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 klai-hover"
          >
            errors.getklai.com
            <ExternalLink className="h-4 w-4" />
          </a>
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white px-5 py-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-[15px] font-display text-gray-900">
              {m.platform_full_service_status()}
            </p>
            <p className="mt-0.5 text-sm text-gray-400">
              {m.platform_full_service_status_description()}
            </p>
          </div>
          <a
            href="https://status.getklai.com"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 klai-hover"
          >
            status.getklai.com
            <ExternalLink className="h-4 w-4" />
          </a>
        </div>
      </div>
    </div>
  )
}

export function UsersTab({
  search,
  fmtDate,
}: {
  search: string
  fmtDate: (s: string | null) => string
}) {
  const navigate = useNavigate()
  const { data, isLoading } = usePlatformUsers(search)
  const rows = data ?? []

  return (
    <PlatformTableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText={m.platform_empty_users()}
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>{m.platform_col_user()}</th>
          <th className={TH}>{m.platform_col_organization()}</th>
          <th className={TH}>{m.platform_col_plan()}</th>
          <th className={TH}>{m.platform_col_created()}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((u) => (
          <tr
            key={u.zitadel_user_id}
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(u.org_id) },
              })
            }
            className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
          >
            <td className={TD}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">
                  {u.display_name || u.email || u.zitadel_user_id}
                </span>
                {u.is_admin && (
                  <Badge variant="secondary">{m.platform_admin()}</Badge>
                )}
              </div>
              {u.email && <p className="text-xs text-gray-400">{u.email}</p>}
            </td>
            <td className={TD}>
              <div className="flex flex-wrap items-center gap-2">
                <span>{u.org_name}</span>
                {!u.org_onboarded && (
                  <Badge variant="outline">{m.platform_not_onboarded()}</Badge>
                )}
              </div>
            </td>
            <td className={TD}>
              <Badge variant="outline">{u.org_plan}</Badge>
            </td>
            <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
              {fmtDate(u.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </PlatformTableShell>
  )
}

export function OrgsTab({
  search,
  fmtDate,
}: {
  search: string
  fmtDate: (s: string | null) => string
}) {
  const navigate = useNavigate()
  const { data, isLoading } = usePlatformOrgs(search)
  const rows = data ?? []

  return (
    <PlatformTableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText={m.platform_empty_organizations()}
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>{m.platform_col_organization()}</th>
          <th className={TH}>{m.platform_col_plan()}</th>
          <th className={TH}>{m.platform_col_users()}</th>
          <th className={TH}>{m.platform_col_bots()}</th>
          <th className={TH}>{m.platform_col_kbs()}</th>
          <th className={TH}>{m.platform_col_status()}</th>
          <th className={TH}>{m.platform_col_created()}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((o) => (
          <tr
            key={o.id}
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(o.id) },
              })
            }
            className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
          >
            <td className={TD}>
              <span className="font-medium">{o.name}</span>
              <p className="font-mono text-xs text-gray-400">{o.slug}</p>
            </td>
            <td className={TD}>
              <Badge variant="outline">{o.plan}</Badge>
            </td>
            <td className={`${TD} tabular-nums`}>{o.user_count}</td>
            <td className={`${TD} tabular-nums`}>{o.bot_count}</td>
            <td className={`${TD} tabular-nums`}>{o.kb_count}</td>
            <td className={TD}>
              <Badge
                variant={
                  o.provisioning_status === 'ready' ? 'success' : 'outline'
                }
              >
                {o.provisioning_status}
              </Badge>
            </td>
            <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
              {fmtDate(o.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </PlatformTableShell>
  )
}

export function SubsTab({ search }: { search: string }) {
  const { data, isLoading } = usePlatformOrgs(search)
  const rows = data ?? []

  return (
    <PlatformTableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText={m.platform_empty_subscriptions()}
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>{m.platform_col_organization()}</th>
          <th className={TH}>{m.platform_col_plan()}</th>
          <th className={TH}>{m.platform_col_cycle()}</th>
          <th className={TH}>{m.platform_col_seats()}</th>
          <th className={TH}>{m.platform_col_billing_status()}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((o) => (
          <tr key={o.id} className="border-b border-gray-200 last:border-b-0">
            <td className={TD}>
              <span className="font-medium">{o.name}</span>
            </td>
            <td className={TD}>
              <Badge variant="outline">{o.plan}</Badge>
            </td>
            <td className={TD}>{o.billing_cycle}</td>
            <td className={`${TD} tabular-nums`}>{o.seats}</td>
            <td className={TD}>
              <Badge
                variant={
                  o.billing_status === 'active' ||
                  o.billing_status === 'trialing'
                    ? 'success'
                    : 'outline'
                }
              >
                {o.billing_status}
              </Badge>
            </td>
          </tr>
        ))}
      </tbody>
    </PlatformTableShell>
  )
}

export function KbTab({
  search,
  fmtDate,
}: {
  search: string
  fmtDate: (s: string | null) => string
}) {
  const navigate = useNavigate()
  const { data, isLoading } = usePlatformKnowledgeBases(search)
  const rows = data ?? []

  return (
    <PlatformTableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText={m.platform_empty_knowledge_bases()}
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>{m.platform_col_knowledge_base()}</th>
          <th className={TH}>{m.platform_col_organization()}</th>
          <th className={TH}>{m.platform_col_type()}</th>
          <th className={TH}>{m.platform_col_visibility()}</th>
          <th className={TH}>{m.platform_col_created()}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((kb) => (
          <tr
            key={kb.id}
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(kb.org_id) },
              })
            }
            className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
          >
            <td className={TD}>
              <span className="font-medium">{kb.name}</span>
              <p className="font-mono text-xs text-gray-400">{kb.slug}</p>
            </td>
            <td className={TD}>{kb.org_name}</td>
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
    </PlatformTableShell>
  )
}

export function TemplatesTab({
  search,
  fmtDate,
}: {
  search: string
  fmtDate: (s: string | null) => string
}) {
  const navigate = useNavigate()
  const { data, isLoading } = usePlatformTemplates(search)
  const rows = data ?? []

  return (
    <PlatformTableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText={m.platform_empty_templates()}
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>{m.platform_col_template()}</th>
          <th className={TH}>{m.platform_col_organization()}</th>
          <th className={TH}>{m.platform_col_scope()}</th>
          <th className={TH}>{m.platform_col_created_by()}</th>
          <th className={TH}>{m.platform_col_created()}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t) => (
          <tr
            key={t.id}
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(t.org_id) },
              })
            }
            className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
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
            <td className={TD}>{t.org_name}</td>
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
    </PlatformTableShell>
  )
}

export function BotsTab({
  search,
  fmtDate,
}: {
  search: string
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformBots(search)
  const rows = data ?? []

  return (
    <PlatformTableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText={m.platform_empty_bots()}
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>{m.platform_col_bot()}</th>
          <th className={TH}>{m.platform_col_organization()}</th>
          <th className={TH}>{m.platform_col_knowledge_bases()}</th>
          <th className={TH}>{m.platform_col_created()}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((b) => (
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
            <td className={TD}>{b.org_name}</td>
            <td className={`${TD} tabular-nums`}>{b.kb_count}</td>
            <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
              {fmtDate(b.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </PlatformTableShell>
  )
}

export function ChatErrorsTab({
  fmtDate,
}: {
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformChatErrors()
  const rows = data ?? []

  return (
    <PlatformTableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText={m.platform_empty_chat_errors()}
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>{m.platform_col_type()}</th>
          <th className={TH}>{m.platform_col_organization()}</th>
          <th className={TH}>{m.platform_col_detail()}</th>
          <th className={TH}>{m.platform_col_time()}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((e) => (
          <tr key={e.id} className="border-b border-gray-200 last:border-b-0">
            <td className={TD}>
              <Badge variant="destructive">{e.event_type}</Badge>
            </td>
            <td className={TD}>{e.org_name ?? `#${e.org_id}`}</td>
            <td className={`${TD} max-w-md truncate text-gray-400`}>
              {e.detail ?? '-'}
            </td>
            <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
              {fmtDate(e.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </PlatformTableShell>
  )
}

function feedbackKindLabel(eventType: string): string {
  if (eventType === 'klai_assistant.question') return m.platform_feedback_kind_question()
  if (eventType === 'klai_assistant.problem_report') return m.platform_feedback_kind_problem()
  return m.platform_feedback_kind_feedback()
}

function feedbackStatusLabel(status: string): string {
  if (status === 'linked') return 'Gekoppeld'
  if (status === 'dismissed') return 'Genegeerd'
  if (status === 'support') return 'Support'
  if (status === 'triage_suggested') return 'Suggestie'
  return 'Nieuw'
}

export function FeedbackTab({
  search,
  fmtDate,
}: {
  search: string
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformFeedbackSubmissions(search)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const rows = data ?? []
  const selected = rows.find((row) => row.id === selectedId) ?? null

  return (
    <>
      <PlatformTableShell
        loading={isLoading}
        empty={rows.length === 0}
        emptyText={m.platform_empty_feedback()}
      >
        <thead>
          <tr className="border-b border-gray-200">
            <th className={TH}>{m.platform_col_type()}</th>
            <th className={TH}>Status</th>
            <th className={TH}>{m.platform_col_organization()}</th>
            <th className={TH}>{m.platform_col_detail()}</th>
            <th className={TH}>{m.platform_feedback_context()}</th>
            <th className={TH}>{m.platform_col_time()}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr
              key={item.id}
              className="cursor-pointer border-b border-gray-200 transition-colors last:border-b-0 hover:bg-gray-50"
              tabIndex={0}
              onClick={() => setSelectedId(item.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') setSelectedId(item.id)
              }}
            >
              <td className={TD}>
                <div className="flex flex-col items-start gap-2">
                  <Badge
                    variant={
                      item.event_type === 'klai_assistant.problem_report'
                        ? 'destructive'
                        : item.event_type === 'klai_assistant.feedback'
                          ? 'success'
                          : 'outline'
                    }
                  >
                    {feedbackKindLabel(item.event_type)}
                  </Badge>
                  {(item.feedback_type || item.severity) && (
                    <span className="text-xs text-gray-400">
                      {item.feedback_type || item.severity}
                    </span>
                  )}
                </div>
              </td>
              <td className={TD}>
                <Badge variant={item.status === 'new' ? 'outline' : 'secondary'}>
                  {feedbackStatusLabel(item.status)}
                </Badge>
              </td>
              <td className={TD}>
                <span className="font-medium">
                  {item.org_name ?? (item.org_id ? `#${item.org_id}` : '-')}
                </span>
                {item.org_slug && (
                  <p className="font-mono text-xs text-gray-400">{item.org_slug}</p>
                )}
                {item.user_id && (
                  <p className="mt-1 max-w-[180px] truncate font-mono text-xs text-gray-400">
                    {item.user_id}
                  </p>
                )}
              </td>
              <td className={`${TD} max-w-md`}>
                <p className="line-clamp-3 whitespace-pre-wrap text-sm leading-6">
                  {item.raw_text ?? '-'}
                </p>
              </td>
              <td className={`${TD} max-w-xs`}>
                {item.route_id && (
                  <p className="font-mono text-xs text-gray-500">{item.route_id}</p>
                )}
                {item.page_url && (
                  <a
                    href={item.page_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-flex max-w-full items-center gap-1 text-xs text-[var(--color-rl-accent-dark)] hover:underline"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <span className="truncate">{item.page_url}</span>
                    <ExternalLink className="h-3 w-3 shrink-0" />
                  </a>
                )}
                {(item.locale || item.viewport) && (
                  <p className="mt-1 text-xs text-gray-400">
                    {[item.locale, item.viewport].filter(Boolean).join(' / ')}
                  </p>
                )}
              </td>
              <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
                {fmtDate(item.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </PlatformTableShell>

      {selected && (
        <FeedbackDetailSheet
          key={selected.id}
          item={selected}
          fmtDate={fmtDate}
          onClose={() => setSelectedId(null)}
        />
      )}
    </>
  )
}

function FeedbackDetailSheet({
  item,
  fmtDate,
  onClose,
}: {
  item: PlatformFeedbackSubmission
  fmtDate: (s: string | null) => string
  onClose: () => void
}) {
  const defaultKind =
    item.event_type === 'klai_assistant.problem_report' ? 'bug' : 'feature'
  const [itemSearch, setItemSearch] = useState(item.raw_text?.slice(0, 80) ?? '')
  const [kind, setKind] = useState(defaultKind)
  const [title, setTitle] = useState(item.raw_text?.slice(0, 90) ?? '')
  const [summary, setSummary] = useState(item.raw_text ?? '')
  const [area, setArea] = useState(item.route_id?.replace(/^\/app\//, '') ?? '')

  const items = usePlatformFeedbackItems(itemSearch)
  const dismiss = usePlatformFeedbackDismiss()
  const support = usePlatformFeedbackSupport()
  const createItem = usePlatformFeedbackCreateItem()
  const linkItem = usePlatformFeedbackLinkItem()
  const busy =
    dismiss.isPending ||
    support.isPending ||
    createItem.isPending ||
    linkItem.isPending
  const canTriage = item.status === 'new' || item.status === 'triage_suggested'

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Feedback triage</SheetTitle>
          <SheetDescription>
            {item.org_name ?? item.org_slug ?? 'Onbekende organisatie'} -{' '}
            {fmtDate(item.created_at)}
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-6">
          <section className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">{feedbackKindLabel(item.event_type)}</Badge>
              <Badge variant={item.status === 'new' ? 'outline' : 'secondary'}>
                {feedbackStatusLabel(item.status)}
              </Badge>
              {(item.feedback_type || item.severity) && (
                <Badge variant="secondary">
                  {item.feedback_type || item.severity}
                </Badge>
              )}
            </div>
            <p className="whitespace-pre-wrap rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm leading-6 text-gray-900">
              {item.raw_text}
            </p>
            <div className="grid gap-2 text-xs text-gray-500">
              {item.user_id && <p className="font-mono">user: {item.user_id}</p>}
              {item.route_id && <p className="font-mono">route: {item.route_id}</p>}
              {item.page_url && (
                <a
                  href={item.page_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[var(--color-rl-accent-dark)] hover:underline"
                >
                  {item.page_url}
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
          </section>

          {canTriage ? (
            <>
              <section className="grid gap-3 sm:grid-cols-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => {
                    dismiss.mutate(item.id, { onSuccess: onClose })
                  }}
                >
                  <ArchiveX className="h-4 w-4" />
                  Negeer
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => {
                    support.mutate(item.id, { onSuccess: onClose })
                  }}
                >
                  <LifeBuoy className="h-4 w-4" />
                  Support
                </Button>
              </section>

              <section className="space-y-3 border-t border-gray-200 pt-5">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-medium text-gray-900">Koppel aan bestaand item</h3>
                  {items.isFetching && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
                </div>
                <Input
                  value={itemSearch}
                  onChange={(event) => setItemSearch(event.target.value)}
                  placeholder="Zoek roadmap item"
                />
                <div className="space-y-2">
                  {(items.data ?? []).map((existing) => (
                    <div
                      key={existing.id}
                      className="flex items-start justify-between gap-3 rounded-lg border border-gray-200 p-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-gray-900">
                          {existing.title}
                        </p>
                        <p className="mt-1 text-xs text-gray-400">
                          {[existing.kind, existing.status, existing.area]
                            .filter(Boolean)
                            .join(' / ')}
                        </p>
                        <p className="mt-1 text-xs text-gray-400">
                          {existing.org_count} orgs - {existing.user_count} users
                        </p>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        disabled={busy}
                        onClick={() => {
                          linkItem.mutate(
                            {
                              submissionId: item.id,
                              item_id: existing.id,
                              link_type:
                                item.event_type === 'klai_assistant.problem_report'
                                  ? 'bug_repro'
                                  : 'evidence',
                            },
                            { onSuccess: onClose },
                          )
                        }}
                      >
                        <Link2 className="h-4 w-4" />
                        Link
                      </Button>
                    </div>
                  ))}
                </div>
              </section>

              <section className="space-y-3 border-t border-gray-200 pt-5">
                <h3 className="text-sm font-medium text-gray-900">Maak nieuw item</h3>
                <Select value={kind} onChange={(event) => setKind(event.target.value)}>
                  <option value="feature">Feature</option>
                  <option value="bug">Bug</option>
                  <option value="ux_confusion">UX verwarring</option>
                  <option value="docs">Docs</option>
                  <option value="support_pattern">Support patroon</option>
                </Select>
                <Input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Titel"
                />
                <Textarea
                  value={summary}
                  onChange={(event) => setSummary(event.target.value)}
                  rows={4}
                  placeholder="Samenvatting"
                />
                <Input
                  value={area}
                  onChange={(event) => setArea(event.target.value)}
                  placeholder="Productgebied"
                />
                <Button
                  type="button"
                  disabled={busy || title.trim().length < 3}
                  onClick={() => {
                    createItem.mutate(
                      {
                        submissionId: item.id,
                        kind,
                        title: title.trim(),
                        summary: summary.trim() || null,
                        area: area.trim() || null,
                        link_type:
                          item.event_type === 'klai_assistant.problem_report'
                            ? 'bug_repro'
                            : 'evidence',
                      },
                      { onSuccess: onClose },
                    )
                  }}
                >
                  <PlusCircle className="h-4 w-4" />
                  Maak item
                </Button>
              </section>
            </>
          ) : (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
              Deze melding is al afgehandeld als {feedbackStatusLabel(item.status).toLowerCase()}.
            </div>
          )}

          {(dismiss.isSuccess || support.isSuccess || createItem.isSuccess || linkItem.isSuccess) && (
            <div className="flex items-center gap-2 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700">
              <CheckCircle2 className="h-4 w-4" />
              Opgeslagen
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
