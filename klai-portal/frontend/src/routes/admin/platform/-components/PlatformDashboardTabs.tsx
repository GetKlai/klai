import { useNavigate } from '@tanstack/react-router'
import { type ReactNode, useState } from 'react'
import {
  Activity,
  ArchiveX,
  ArrowLeft,
  ArrowRight,
  Bug,
  CheckCircle2,
  Copy,
  ExternalLink,
  LifeBuoy,
  Link2,
  Loader2,
  Pencil,
  PlusCircle,
  Save,
  Search,
  Trash2,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import { Textarea } from '@/components/ui/textarea'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import * as m from '@/paraglide/messages'
import {
  usePlatformBots,
  usePlatformChatErrors,
  usePlatformFeedbackCreateItem,
  usePlatformFeedbackDeleteSubmission,
  usePlatformFeedbackDeleteItem,
  usePlatformFeedbackDismiss,
  usePlatformFeedbackItem,
  usePlatformFeedbackItems,
  usePlatformFeedbackLinkItem,
  usePlatformFeedbackResolveItem,
  usePlatformFeedbackSubmissions,
  usePlatformFeedbackSupport,
  usePlatformFeedbackUpdateSubmission,
  usePlatformFeedbackUpdateItem,
  usePlatformKnowledgeBases,
  usePlatformOrgs,
  usePlatformSubdomains,
  usePlatformTemplates,
  usePlatformUsers,
  usePortalHealth,
} from '../-hooks'
import type {
  PlatformFeedbackItem,
  PlatformFeedbackLinkedSubmission,
  PlatformFeedbackSubmission,
  PlatformSubdomainItem,
} from '../-types'
import { PlatformTableShell } from './PlatformShell'

const TH =
  'py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide whitespace-nowrap'
const TD = 'py-3.5 pr-4 align-top text-gray-900'
const CLOSED_FEEDBACK_ITEM_STATUSES = new Set(['resolved', 'dismissed'])

// --- Subdomains overview ---------------------------------------------------
//
// Catalogue of every Klai-controlled subdomain (Caddyfile + Coolify +
// Hetzner DNS) so we can spot when a service drops off the radar. Combines
// the curated static list with live tenant entries from portal_orgs, plus
// a 3s liveness probe per URL.

const SUBDOMAIN_SECTIONS: {
  category: PlatformSubdomainItem['category']
  title: () => string
  description: () => string
}[] = [
  {
    category: 'klai_service',
    title: () => m.platform_subdomains_section_klai_services(),
    description: () => m.platform_subdomains_section_klai_services_description(),
  },
  {
    category: 'tooling',
    title: () => m.platform_subdomains_section_tooling(),
    description: () => m.platform_subdomains_section_tooling_description(),
  },
  {
    category: 'marketing',
    title: () => m.platform_subdomains_section_marketing(),
    description: () => m.platform_subdomains_section_marketing_description(),
  },
  {
    category: 'tenant',
    title: () => m.platform_subdomains_section_tenants(),
    description: () => m.platform_subdomains_section_tenants_description(),
  },
]

function SubdomainStatusBadge({ item }: { item: PlatformSubdomainItem }) {
  const code = item.status_code !== null ? ` ${item.status_code}` : ''
  switch (item.status) {
    case 'up':
      return <Badge variant="success">{m.platform_subdomains_status_up()}{code}</Badge>
    case 'auth_required':
      return <Badge variant="secondary">{m.platform_subdomains_status_auth()}{code}</Badge>
    case 'client_error':
      return <Badge variant="secondary">{m.platform_subdomains_status_client_error()}{code}</Badge>
    case 'server_error':
      return <Badge variant="destructive">{m.platform_subdomains_status_server_error()}{code}</Badge>
    case 'unreachable':
      return <Badge variant="destructive">{m.platform_subdomains_status_unreachable()}</Badge>
    case 'not_probed':
      return <Badge variant="outline">{m.platform_subdomains_status_not_probed()}</Badge>
  }
}

export function SubdomainsTab({ search }: { search: string }) {
  const { data, isLoading, isError } = usePlatformSubdomains()
  const items = data ?? []
  const needle = search.trim().toLowerCase()
  const filtered = needle
    ? items.filter(
        (i) =>
          i.subdomain.toLowerCase().includes(needle) ||
          i.label.toLowerCase().includes(needle) ||
          i.url.toLowerCase().includes(needle) ||
          i.description.toLowerCase().includes(needle) ||
          i.owner.toLowerCase().includes(needle),
      )
    : items

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-gray-400">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        {m.platform_subdomains_loading()}
      </div>
    )
  }
  if (isError) {
    return (
      <p className="py-16 text-center text-sm text-[var(--color-destructive)]">
        {m.platform_subdomains_load_error()}
      </p>
    )
  }
  if (filtered.length === 0) {
    return <p className="py-16 text-center text-sm text-gray-400">{m.platform_subdomains_empty()}</p>
  }

  return (
    <div className="space-y-10">
      {SUBDOMAIN_SECTIONS.map((section) => {
        const rows = filtered.filter((i) => i.category === section.category)
        if (rows.length === 0) return null
        return (
          <section key={section.category} className="space-y-3">
            <div>
              <h2 className="text-[15px] font-display-bold text-gray-900">{section.title()}</h2>
              <p className="text-sm text-gray-400">{section.description()}</p>
            </div>
            <PlatformTableShell
              loading={false}
              empty={false}
              emptyText=""
            >
              <thead>
                <tr className="border-b border-gray-200">
                  <th className={TH}>{m.platform_subdomains_col_subdomain()}</th>
                  <th className={TH}>{m.platform_subdomains_col_label()}</th>
                  <th className={TH}>{m.platform_subdomains_col_host()}</th>
                  <th className={TH}>{m.platform_subdomains_col_owner()}</th>
                  <th className={TH}>{m.platform_subdomains_col_status()}</th>
                  <th className={TH}></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.url} className="border-b border-gray-200 last:border-b-0">
                    <td className={TD}>
                      <p className="font-mono text-xs text-gray-900">{item.subdomain || '(apex)'}</p>
                      <p className="mt-1 text-xs text-gray-400">{item.description}</p>
                    </td>
                    <td className={TD}>
                      <span className="text-sm">{item.label}</span>
                    </td>
                    <td className={TD}>
                      <span className="text-xs font-mono text-gray-400">{item.host}</span>
                    </td>
                    <td className={TD}>
                      <span className="text-sm text-gray-700">{item.owner}</span>
                    </td>
                    <td className={TD}>
                      <SubdomainStatusBadge item={item} />
                    </td>
                    <td className={TD + ' text-right'}>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 text-sm text-[var(--color-rl-accent-dark)] hover:underline"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </PlatformTableShell>
          </section>
        )
      })}
    </div>
  )
}

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

function feedbackSubmissionReporterLabel(item: PlatformFeedbackSubmission): string | null {
  return item.user_display_name || item.user_email || item.user_id || null
}

function feedbackStatusLabel(status: string): string {
  if (status === 'open') return m.platform_feedback_status_open()
  if (status === 'resolved') return m.platform_feedback_status_resolved()
  if (status === 'dismissed') return m.platform_feedback_status_dismissed()
  if (status === 'support') return m.platform_feedback_status_support()
  return m.platform_feedback_status_new()
}

function feedbackItemStatusLabel(status: string): string {
  if (status === 'resolved') return m.platform_feedback_status_resolved()
  if (status === 'dismissed') return m.platform_feedback_status_dismissed()
  return m.platform_feedback_status_open()
}

function feedbackItemReporterSummary(item: PlatformFeedbackItem): string {
  const names = item.reporter_orgs
    .map((org) => org.org_name ?? org.org_slug ?? (org.org_id ? `#${org.org_id}` : null))
    .filter((name): name is string => Boolean(name))

  if (names.length === 0) {
    return item.org_count > 0
      ? m.platform_feedback_org_count({ count: item.org_count })
      : '-'
  }
  if (names.length <= 2) return names.join(', ')
  return `${names.slice(0, 2).join(', ')} +${names.length - 2}`
}

function feedbackItemKindLabel(kind: string): string {
  if (kind === 'bug') return m.platform_feedback_item_kind_bug()
  if (kind === 'ux_confusion') return m.platform_feedback_item_kind_ux()
  if (kind === 'docs') return m.platform_feedback_item_kind_docs()
  if (kind === 'support_pattern') return m.platform_feedback_item_kind_support()
  return m.platform_feedback_item_kind_feature()
}

function feedbackSuggestionActionLabel(action: string | null | undefined): string {
  if (action === 'link_existing') return m.platform_feedback_action_link_existing()
  if (action === 'create_item') return m.platform_feedback_action_create_item()
  if (action === 'support') return m.platform_feedback_action_support()
  if (action === 'dismiss') return m.platform_feedback_action_dismiss()
  if (action === 'review') return m.platform_feedback_action_review()
  return m.platform_feedback_action_review()
}

function feedbackSuggestionPrimaryLabel(
  action: string | null | undefined,
  candidateTitle: string | null | undefined,
  kind: string,
): string {
  if (action === 'link_existing') {
    const shortTitle =
      candidateTitle && candidateTitle.length > 44
        ? `${candidateTitle.slice(0, 41)}...`
        : candidateTitle
    return shortTitle
      ? m.platform_feedback_primary_link_to({ title: shortTitle })
      : m.platform_feedback_primary_link_existing()
  }
  if (action === 'support') return m.platform_feedback_primary_support()
  if (action === 'dismiss') return m.platform_feedback_primary_dismiss()
  if (action === 'review') return m.platform_feedback_primary_review()
  return m.platform_feedback_primary_create({ kind: feedbackItemKindLabel(kind).toLowerCase() })
}

function feedbackItemSearchTerm(
  item: PlatformFeedbackSubmission,
  suggestion: PlatformFeedbackSubmission['triage_suggestion'],
): string {
  const candidateTitle = suggestion?.duplicate_candidates[0]?.title
  if (candidateTitle) return candidateTitle.slice(0, 80)

  const source = suggestion?.summary || item.raw_text || suggestion?.suggested_area || ''
  const words = source
    .toLowerCase()
    .replace(/[^a-z0-9_ -]+/g, ' ')
    .split(/\s+/)
    .filter((word) => word.length >= 4)
    .filter(
      (word) =>
        ![
          'voor',
          'door',
          'naar',
          'niet',
          'geen',
          'deze',
          'daar',
          'hier',
          'kunnen',
          'willen',
          'moeten',
          'zodat',
          'voordat',
          'soms',
          'eens',
          'with',
          'from',
          'that',
          'this',
        ].includes(word),
    )

  const search = words.slice(0, 2).join(' ')
  return search || source.slice(0, 80)
}

function feedbackFallbackSummary(item: PlatformFeedbackSubmission): string {
  if (item.event_type === 'klai_assistant.problem_report') {
    return m.platform_feedback_fallback_bug({
      text: item.raw_text || m.platform_feedback_no_description(),
    })
  }
  return item.raw_text || m.platform_feedback_no_description()
}

function normalizedFeedbackKind(kind: string | null | undefined, fallback: string): string {
  if (
    kind &&
    ['feature', 'bug', 'ux_confusion', 'docs', 'support_pattern'].includes(kind)
  ) {
    return kind
  }
  return fallback
}

export function FeedbackTab({
  search,
  status,
  kind,
  fmtDate,
}: {
  search: string
  status: string
  kind: string
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformFeedbackSubmissions(search, status, kind)
  const navigate = useNavigate()
  const [feedbackView, setFeedbackView] = useState<'inbox' | 'items'>('inbox')
  const rows = data ?? []

  return (
    <>
      <div className="mb-5 inline-flex rounded-lg border border-gray-200 bg-white p-1">
        <Button
          type="button"
          size="sm"
          variant={feedbackView === 'inbox' ? 'default' : 'ghost'}
          onClick={() => setFeedbackView('inbox')}
        >
          {m.platform_feedback_view_inbox()}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={feedbackView === 'items' ? 'default' : 'ghost'}
          onClick={() => setFeedbackView('items')}
        >
          {m.platform_feedback_view_items()}
        </Button>
      </div>

      {feedbackView === 'items' ? (
        <OpenItemsPanel
          search={search}
          fmtDate={fmtDate}
          onOpenItem={(itemId) =>
            void navigate({
              to: '/admin/platform/feedback/items/$itemId',
              params: { itemId: String(itemId) },
            })
          }
        />
      ) : (
        <PlatformTableShell
          loading={isLoading}
          empty={rows.length === 0}
          emptyText={m.platform_empty_feedback()}
        >
          <thead>
            <tr className="border-b border-gray-200">
              <th className={TH}>{m.platform_col_type()}</th>
              <th className={TH}>{m.platform_col_status()}</th>
              <th className={TH}>{m.platform_col_organization()}</th>
              <th className={TH}>{m.platform_col_detail()}</th>
              <th className={TH}>{m.platform_col_time()}</th>
              <th className={TH}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <FeedbackSubmissionRow
                key={item.id}
                item={item}
                fmtDate={fmtDate}
                onOpen={() =>
                  void navigate({
                    to: '/admin/platform/feedback/submissions/$submissionId',
                    params: { submissionId: String(item.id) },
                  })
                }
              />
            ))}
          </tbody>
        </PlatformTableShell>
      )}
    </>
  )
}

function FeedbackSubmissionRow({
  item,
  fmtDate,
  onOpen,
}: {
  item: PlatformFeedbackSubmission
  fmtDate: (s: string | null) => string
  onOpen: () => void
}) {
  return (
    <tr
      className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
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
        {feedbackSubmissionReporterLabel(item) && (
          <p className="mt-1 max-w-[180px] truncate text-xs text-gray-400">
            {feedbackSubmissionReporterLabel(item)}
          </p>
        )}
      </td>
      <td className={`${TD} max-w-md`}>
        <p className="line-clamp-3 whitespace-pre-wrap text-sm leading-6">
          {item.raw_text ?? '-'}
        </p>
      </td>
      <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
        {fmtDate(item.created_at)}
      </td>
      <td className={`${TD} text-right`}>
        <button
          type="button"
          className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
          title={m.platform_feedback_edit()}
          aria-label={m.platform_feedback_edit()}
          onClick={(event) => {
            event.stopPropagation()
            onOpen()
          }}
        >
          <Pencil className="h-4 w-4" />
        </button>
      </td>
    </tr>
  )
}

function OpenItemsPanel({
  search,
  fmtDate,
  onOpenItem,
}: {
  search: string
  fmtDate: (s: string | null) => string
  onOpenItem: (itemId: number) => void
}) {
  const [statusFilter, setStatusFilter] = useState('active')
  const [kindFilter, setKindFilter] = useState('all')
  const items = usePlatformFeedbackItems(search, statusFilter, kindFilter)
  const rows = items.data ?? []

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="h-9 min-w-[150px]"
            aria-label={m.platform_feedback_filter_status()}
          >
            <option value="active">{m.platform_feedback_filter_active()}</option>
            <option value="open">{m.platform_feedback_status_open()}</option>
            <option value="resolved">{m.platform_feedback_status_resolved()}</option>
            <option value="dismissed">{m.platform_feedback_status_dismissed()}</option>
            <option value="all">{m.platform_feedback_filter_all()}</option>
          </Select>
          <Select
            value={kindFilter}
            onChange={(event) => setKindFilter(event.target.value)}
            className="h-9 min-w-[130px]"
            aria-label={m.platform_feedback_filter_type()}
          >
            <option value="all">{m.platform_feedback_filter_all_types()}</option>
            <option value="bug">{m.platform_feedback_item_kind_bug()}</option>
            <option value="feature">{m.platform_feedback_item_kind_feature()}</option>
            <option value="ux_confusion">{m.platform_feedback_item_kind_ux()}</option>
            <option value="docs">{m.platform_feedback_item_kind_docs()}</option>
            <option value="support_pattern">{m.platform_feedback_item_kind_support()}</option>
          </Select>
        </div>
        <p className="text-xs text-gray-400">
          {m.platform_feedback_closed_hidden_hint()}
        </p>
      </div>
      {items.isFetching && !items.isLoading && (
        <p className="mb-2 text-xs text-gray-400">
          <Loader2 className="mr-2 inline h-3 w-3 animate-spin" />
          {m.platform_feedback_items_refreshing()}
        </p>
      )}
      <PlatformTableShell
        loading={items.isLoading}
        empty={rows.length === 0}
        emptyText={m.platform_feedback_items_empty()}
      >
          <thead>
            <tr className="border-b border-gray-200">
              <th className={TH}>{m.platform_feedback_col_item()}</th>
              <th className={TH}>{m.platform_feedback_col_organizations()}</th>
              <th className={TH}>{m.platform_col_status()}</th>
              <th className={TH}>{m.platform_col_type()}</th>
              <th className={TH}>{m.platform_feedback_col_updated()}</th>
              <th className={TH}></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {rows.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
                  tabIndex={0}
                  onClick={() => onOpenItem(item.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') onOpenItem(item.id)
                  }}
                >
                  <td className={`${TD} max-w-xl`}>
                    <span className="block truncate font-medium text-gray-900">
                      {item.title}
                    </span>
                    <span className="mt-1 block truncate text-xs text-gray-400">
                      {[item.area, item.owner && m.platform_feedback_owner({ owner: item.owner })]
                        .filter(Boolean)
                        .join(' / ')}
                    </span>
                  </td>
                  <td className={`${TD} max-w-xs`}>
                    <span className="block truncate text-sm text-gray-900">
                      {feedbackItemReporterSummary(item)}
                    </span>
                    <span className="mt-1 block text-xs text-gray-400">
                      {m.platform_feedback_reporter_counts({
                        orgs: item.org_count,
                        users: item.user_count,
                      })}
                    </span>
                  </td>
                  <td className={TD}>
                    <Badge variant="outline">{feedbackItemStatusLabel(item.status)}</Badge>
                  </td>
                  <td className={TD}>{feedbackItemKindLabel(item.kind)}</td>
                  <td className={`${TD} whitespace-nowrap text-gray-400`}>
                    {fmtDate(item.updated_at)}
                  </td>
                  <td className={`${TD} text-right`}>
                    <button
                      type="button"
                      className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                      title={m.platform_feedback_edit()}
                      aria-label={m.platform_feedback_edit_item({ title: item.title })}
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpenItem(item.id)
                      }}
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
      </PlatformTableShell>
    </>
  )
}

export function FeedbackSubmissionDetailPanel({
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
  const suggestion = item.triage_suggestion
  const bestCandidate = suggestion?.duplicate_candidates[0] ?? null
  const suggestedKind = normalizedFeedbackKind(suggestion?.classification, defaultKind)
  const suggestedTitle = (suggestion?.summary || item.raw_text || '').slice(0, 90)
  const suggestedSearch = feedbackItemSearchTerm(item, suggestion)
  const [itemSearch, setItemSearch] = useState(suggestedSearch)
  const [kind, setKind] = useState(suggestedKind)
  const [title, setTitle] = useState(suggestedTitle)
  const [summary, setSummary] = useState(suggestion?.summary ?? item.raw_text ?? '')
  const [area, setArea] = useState(suggestion?.suggested_area ?? '')
  const [draftRawText, setDraftRawText] = useState(item.raw_text ?? '')
  const [draftStatus, setDraftStatus] = useState(item.status)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [triageAction, setTriageAction] = useState<
    'recommended' | 'link' | 'create' | 'support' | 'dismiss'
  >('recommended')
  const [submissionStep, setSubmissionStep] = useState<'report' | 'proposal' | 'decision'>(
    'report',
  )

  const items = usePlatformFeedbackItems(itemSearch, 'triage')
  const existingItems = items.data ?? []
  const bestSearchMatch = itemSearch.trim().length >= 4 ? (existingItems[0] ?? null) : null
  const updateSubmission = usePlatformFeedbackUpdateSubmission()
  const deleteSubmission = usePlatformFeedbackDeleteSubmission()
  const dismiss = usePlatformFeedbackDismiss()
  const support = usePlatformFeedbackSupport()
  const createItem = usePlatformFeedbackCreateItem()
  const linkItem = usePlatformFeedbackLinkItem()
  const busy =
    updateSubmission.isPending ||
    deleteSubmission.isPending ||
    dismiss.isPending ||
    support.isPending ||
    createItem.isPending ||
    linkItem.isPending
  const canTriage = draftStatus === 'new'
  const linkType =
    item.event_type === 'klai_assistant.problem_report'
      ? 'bug_repro'
      : suggestion?.classification === 'support_pattern'
        ? 'support_signal'
        : 'evidence'
  const recommendedAction =
    bestCandidate || bestSearchMatch
      ? 'link_existing'
      : items.isFetching
        ? 'review'
      : suggestion?.suggested_action || 'create_item'
  const recommendedItem = bestCandidate
    ? {
        id: bestCandidate.item_id,
        title: bestCandidate.title ?? `Item #${bestCandidate.item_id}`,
        kind: bestCandidate.kind,
        status: bestCandidate.status,
        area: bestCandidate.area,
      }
    : bestSearchMatch
      ? {
          id: bestSearchMatch.id,
          title: bestSearchMatch.title,
          kind: bestSearchMatch.kind,
          status: bestSearchMatch.status,
          area: bestSearchMatch.area,
        }
      : null
  const proposalSummary = suggestion?.summary || draftRawText || feedbackFallbackSummary(item)
  const saveSubmission = () => {
    updateSubmission.mutate({
      submissionId: item.id,
      raw_text: draftRawText.trim(),
      status: draftStatus,
    })
  }
  const acceptCreateItem = () => {
    const fallbackTitle = (suggestion?.summary || draftRawText || '').slice(0, 90)
    createItem.mutate(
      {
        submissionId: item.id,
        kind: suggestedKind,
        title: fallbackTitle.trim(),
        summary: draftRawText || suggestion?.summary || null,
        area: suggestion?.suggested_area || area.trim() || null,
        link_type: linkType,
      },
      { onSuccess: onClose },
    )
  }
  const acceptRecommendedAction = () => {
    if (recommendedAction === 'link_existing' && recommendedItem) {
      linkItem.mutate(
        {
          submissionId: item.id,
          item_id: recommendedItem.id,
          link_type: linkType,
        },
        { onSuccess: onClose },
      )
      return
    }
    if (recommendedAction === 'support') {
      support.mutate(item.id, { onSuccess: onClose })
      return
    }
    if (recommendedAction === 'dismiss') {
      dismiss.mutate(item.id, { onSuccess: onClose })
      return
    }
    acceptCreateItem()
  }
  const canAcceptRecommendedAction =
    (recommendedAction !== 'link_existing' || recommendedItem !== null) &&
    (recommendedAction !== 'create_item' || suggestedTitle.trim().length >= 3) &&
    recommendedAction !== 'review'
  const selectedActionClass =
    'border-gray-900 bg-gray-900 text-white hover:bg-gray-800 hover:text-white'
  const actionButtonClass =
    'h-auto min-h-14 justify-start rounded-lg px-4 py-3 text-left whitespace-normal'
  const submissionStepOrder: Array<'report' | 'proposal' | 'decision'> = [
    'report',
    'proposal',
    'decision',
  ]
  const submissionStepIndex = Math.max(0, submissionStepOrder.indexOf(submissionStep))
  const submissionWizardSteps: StepItem[] = submissionStepOrder.map((step) => ({
    label:
      step === 'report'
        ? m.platform_feedback_submission_placeholder()
        : step === 'proposal'
          ? m.platform_feedback_suggestion_title()
          : m.platform_feedback_choose_action_title(),
    onClick: () => setSubmissionStep(step),
  }))
  const previousSubmissionStep = () => {
    if (submissionStepIndex > 0) setSubmissionStep(submissionStepOrder[submissionStepIndex - 1])
  }
  const nextSubmissionStep = () => {
    if (submissionStepIndex < submissionStepOrder.length - 1) {
      setSubmissionStep(submissionStepOrder[submissionStepIndex + 1])
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.platform_feedback_triage_title()}
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            {item.org_name ?? item.org_slug ?? m.platform_feedback_unknown_organization()} -{' '}
            {feedbackSubmissionReporterLabel(item)
              ? `${feedbackSubmissionReporterLabel(item)} - `
              : ''}
            {fmtDate(item.created_at)}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.platform_back_to_feedback()}
        </Button>
      </div>

      <div className="space-y-8">
        <StepIndicator steps={submissionWizardSteps} currentIndex={submissionStepIndex} />

        {submissionStep === 'report' && (
        <section className="space-y-4 border-t border-gray-200 pt-6">
          <div>
            <h2 className="mt-1 text-base font-display-bold text-gray-900">
              {m.platform_feedback_read_report_title()}
            </h2>
          </div>
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
          <Textarea
            id={`feedback-submission-${item.id}`}
            value={draftRawText}
            onChange={(event) => setDraftRawText(event.target.value)}
            rows={6}
            placeholder={m.platform_feedback_submission_placeholder()}
          />
          <div className="grid gap-3 text-sm text-gray-600 sm:grid-cols-2">
            <FeedbackMetaRow
              label={m.platform_col_organization()}
              value={item.org_name ?? item.org_slug ?? m.platform_feedback_unknown_organization()}
            />
            <FeedbackMetaRow
              label={m.platform_feedback_reporter_label()}
              value={feedbackSubmissionReporterLabel(item) ?? '-'}
            />
            <FeedbackMetaRow label="URL" value={item.page_url ?? '-'} />
            <FeedbackMetaRow label="Route" value={item.route_id ?? '-'} />
            <FeedbackMetaRow
              label={m.platform_feedback_context()}
              value={[item.locale, item.viewport].filter(Boolean).join(' / ') || '-'}
            />
            <FeedbackMetaRow label={m.platform_col_created()} value={fmtDate(item.created_at)} />
          </div>
        </section>
        )}

          {canTriage ? (
            <>
              {submissionStep === 'proposal' && (
              <section className="space-y-4 border-t border-gray-200 pt-6">
                <div>
                  <h2 className="mt-1 text-base font-display-bold text-gray-900">
                    {m.platform_feedback_triage_proposal_title()}
                  </h2>
                  <p className="mt-2 text-sm leading-6 text-gray-700">{proposalSummary}</p>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                  <Badge variant="outline">
                    {m.platform_feedback_suggestion_action({
                      action: feedbackSuggestionActionLabel(recommendedAction),
                    })}
                  </Badge>
                  <Badge variant="outline">
                    {m.platform_feedback_suggestion_type({
                      type: feedbackItemKindLabel(suggestedKind),
                    })}
                  </Badge>
                  {suggestion?.suggested_area && (
                    <Badge variant="outline">
                      {m.platform_feedback_suggestion_area({
                        area: suggestion.suggested_area,
                      })}
                    </Badge>
                  )}
                  {suggestion?.suggested_severity && (
                    <Badge variant="outline">
                      {m.platform_feedback_suggestion_severity({
                        severity: suggestion.suggested_severity,
                      })}
                    </Badge>
                  )}
                </div>
                {recommendedItem && (
                  <div className="rounded-lg border border-gray-200 px-4 py-3">
                    <p className="text-xs font-medium text-gray-500">
                      {m.platform_feedback_existing_item_found()}
                    </p>
                    <p className="mt-1 truncate text-sm font-medium text-gray-900">
                      {recommendedItem.title}
                    </p>
                    <p className="mt-1 text-xs text-gray-400">
                      {[recommendedItem.kind, recommendedItem.status, recommendedItem.area]
                        .filter(Boolean)
                        .join(' / ')}
                    </p>
                  </div>
                )}
              </section>
              )}

              {submissionStep === 'decision' && (
              <>
              <section className="space-y-4 border-t border-gray-200 pt-6">
                <div>
                  <h2 className="mt-1 text-base font-display-bold text-gray-900">
                    {m.platform_feedback_choose_action_title()}
                  </h2>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'recommended' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('recommended')}
                  >
                    <CheckCircle2 className="h-4 w-4 shrink-0" />
                    {feedbackSuggestionPrimaryLabel(
                      recommendedAction,
                      recommendedItem?.title,
                      suggestedKind,
                    )}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'link' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('link')}
                  >
                    <Link2 className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_link_existing_title()}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'create' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('create')}
                  >
                    <PlusCircle className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_create_new_title()}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'support' ? selectedActionClass : ''
                    }`}
                    onClick={() => setTriageAction('support')}
                    disabled={busy}
                  >
                    <LifeBuoy className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_primary_support()}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className={`${actionButtonClass} ${
                      triageAction === 'dismiss' ? selectedActionClass : ''
                    } md:col-span-2`}
                    onClick={() => setTriageAction('dismiss')}
                  >
                    <ArchiveX className="h-4 w-4 shrink-0" />
                    {m.platform_feedback_action_dismiss()}
                  </Button>
                </div>
              </section>

              <section className="space-y-4 border-t border-gray-200 pt-6">
                {triageAction === 'recommended' && (
                  <div className="space-y-3">
                    <h3 className="text-sm font-medium text-gray-900">
                      {m.platform_feedback_recommended_action_title()}
                    </h3>
                    <Button
                      type="button"
                      disabled={busy || !canAcceptRecommendedAction || items.isFetching}
                      onClick={acceptRecommendedAction}
                    >
                      {items.isFetching ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4" />
                      )}
                      {feedbackSuggestionPrimaryLabel(
                        recommendedAction,
                        recommendedItem?.title,
                        suggestedKind,
                      )}
                    </Button>
                  </div>
                )}

                {triageAction === 'support' && (
                  <div className="space-y-3">
                    <p className="text-sm leading-6 text-gray-600">
                      {m.platform_feedback_support_help()}
                    </p>
                    <Button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        support.mutate(item.id, { onSuccess: onClose })
                      }}
                    >
                      <LifeBuoy className="h-4 w-4" />
                      {m.platform_feedback_primary_support()}
                    </Button>
                  </div>
                )}

                {triageAction === 'dismiss' && (
                  <div className="space-y-3">
                    <p className="text-sm leading-6 text-gray-600">
                      {m.platform_feedback_dismiss_help()}
                    </p>
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={busy}
                      onClick={() => {
                        dismiss.mutate(item.id, { onSuccess: onClose })
                      }}
                    >
                      <ArchiveX className="h-4 w-4" />
                      {m.platform_feedback_action_dismiss()}
                    </Button>
                  </div>
                )}

                {triageAction === 'link' && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between gap-3">
                      <h3 className="text-sm font-medium text-gray-900">
                        {m.platform_feedback_link_existing_title()}
                      </h3>
                      {items.isFetching && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor={`feedback-item-search-${item.id}`}>
                        {m.platform_feedback_smart_search_label()}
                      </Label>
                      <span className="relative block">
                        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                        <Input
                          id={`feedback-item-search-${item.id}`}
                          value={itemSearch}
                          onChange={(event) => setItemSearch(event.target.value)}
                          placeholder={m.platform_feedback_search_placeholder()}
                          className="pl-9"
                        />
                      </span>
                    </div>
                    <div className="space-y-2">
                      {(items.data ?? []).map((existing) => (
                        <div
                          key={existing.id}
                          className="flex items-start justify-between gap-3 border-t border-gray-200 py-3 first:border-t-0"
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
                              {m.platform_feedback_reporter_counts({
                                orgs: existing.org_count,
                                users: existing.user_count,
                              })}
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
                                  link_type: linkType,
                                },
                                { onSuccess: onClose },
                              )
                            }}
                          >
                            <Link2 className="h-4 w-4" />
                            {m.platform_feedback_link()}
                          </Button>
                        </div>
                      ))}
                      {!items.isFetching && (items.data ?? []).length === 0 && (
                        <p className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-500">
                          {m.platform_feedback_no_existing_item()}
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {triageAction === 'create' && (
                  <div className="space-y-3">
                    <h3 className="text-sm font-medium text-gray-900">
                      {m.platform_feedback_create_new_title()}
                    </h3>
                    <Select value={kind} onChange={(event) => setKind(event.target.value)}>
                      <option value="feature">{m.platform_feedback_item_kind_feature()}</option>
                      <option value="bug">{m.platform_feedback_item_kind_bug()}</option>
                      <option value="ux_confusion">{m.platform_feedback_item_kind_ux()}</option>
                      <option value="docs">{m.platform_feedback_item_kind_docs()}</option>
                      <option value="support_pattern">{m.platform_feedback_item_kind_support()}</option>
                    </Select>
                    <Input
                      value={title}
                      onChange={(event) => setTitle(event.target.value)}
                      placeholder={m.platform_feedback_title_placeholder()}
                    />
                    <Textarea
                      value={summary}
                      onChange={(event) => setSummary(event.target.value)}
                      rows={4}
                      placeholder={m.platform_feedback_summary_placeholder()}
                    />
                    <Input
                      value={area}
                      onChange={(event) => setArea(event.target.value)}
                      placeholder={m.platform_feedback_area_placeholder()}
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
                            link_type: linkType,
                          },
                          { onSuccess: onClose },
                        )
                      }}
                    >
                      <PlusCircle className="h-4 w-4" />
                      {m.platform_feedback_create_item()}
                    </Button>
                  </div>
                )}
              </section>
              </>
              )}
            </>
          ) : (
            <div className="rounded-lg border border-gray-200 px-4 py-3 text-sm text-gray-600">
              {m.platform_feedback_submission_already_handled({
                status: feedbackStatusLabel(item.status).toLowerCase(),
              })}
            </div>
          )}

          {(dismiss.isSuccess || support.isSuccess || createItem.isSuccess || linkItem.isSuccess) && (
            <div className="flex items-center gap-2 rounded-lg bg-[var(--color-success-bg)] px-3 py-2 text-sm text-[var(--color-success-text)]">
              <CheckCircle2 className="h-4 w-4" />
              {m.admin_settings_saved()}
            </div>
          )}

          {submissionStep === 'report' && (
            <section className="grid gap-3 border-t border-gray-200 pt-6 sm:grid-cols-[1fr_auto_auto] sm:items-end">
              <div className="space-y-1.5">
                <Label htmlFor={`feedback-submission-status-${item.id}`}>
                  {m.platform_col_status()}
                </Label>
                <Select
                  id={`feedback-submission-status-${item.id}`}
                  value={draftStatus}
                  onChange={(event) => setDraftStatus(event.target.value)}
                >
                  <option value="new">{m.platform_feedback_status_new()}</option>
                  <option value="open">{m.platform_feedback_status_open()}</option>
                  <option value="resolved">{m.platform_feedback_status_resolved()}</option>
                  <option value="support">{m.platform_feedback_status_support()}</option>
                  <option value="dismissed">{m.platform_feedback_status_dismissed()}</option>
                </Select>
              </div>
              <Button
                type="button"
                disabled={
                  busy ||
                  draftRawText.trim().length < 1 ||
                  (draftRawText.trim() === (item.raw_text ?? '') && draftStatus === item.status)
                }
                onClick={saveSubmission}
              >
                {updateSubmission.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                {m.admin_shared_save()}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => setConfirmDeleteOpen(true)}
              >
                {deleteSubmission.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4" />
                )}
                {m.platform_delete()}
              </Button>
              {updateSubmission.isSuccess && (
                <p className="text-sm text-[var(--color-success)] sm:col-span-3">
                  {m.platform_feedback_submission_saved()}
                </p>
              )}
            </section>
          )}

          <div className="flex items-center justify-between border-t border-gray-200 pt-6">
            <Button
              type="button"
              variant="ghost"
              disabled={submissionStepIndex === 0}
              onClick={previousSubmissionStep}
            >
              <ArrowLeft className="h-4 w-4" />
              {m.admin_shared_wizard_previous()}
            </Button>
            {canTriage && submissionStepIndex < submissionStepOrder.length - 1 && (
              <Button type="button" onClick={nextSubmissionStep}>
                {m.admin_shared_wizard_next()}
                <ArrowRight className="h-4 w-4" />
              </Button>
            )}
          </div>
      </div>
      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {m.platform_feedback_delete_submission_title()}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {m.platform_feedback_delete_submission_description()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                deleteSubmission.mutate(item.id, { onSuccess: onClose })
              }}
            >
              {m.platform_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

export function FeedbackItemDetailPanel({
  itemId,
  fmtDate,
  onClose,
}: {
  itemId: number
  fmtDate: (s: string | null) => string
  onClose: () => void
}) {
  const detail = usePlatformFeedbackItem(itemId)

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.platform_feedback_item_title()}
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            {m.platform_feedback_item_description()}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.platform_back_to_feedback()}
        </Button>
      </div>

      {detail.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          {m.admin_shared_loading()}
        </div>
      ) : detail.data ? (
        <FeedbackItemDetailForm
          key={detail.data.item.id}
          item={detail.data.item}
          submissions={detail.data.submissions}
          fmtDate={fmtDate}
          onClose={onClose}
        />
      ) : (
        <p className="text-sm text-gray-500">{m.platform_feedback_item_not_found()}</p>
      )}
    </div>
  )
}

function FeedbackItemDetailForm({
  item,
  submissions,
  fmtDate,
  onClose,
}: {
  item: PlatformFeedbackItem
  submissions: PlatformFeedbackLinkedSubmission[]
  fmtDate: (s: string | null) => string
  onClose: () => void
}) {
  const updateItem = usePlatformFeedbackUpdateItem()
  const resolveItem = usePlatformFeedbackResolveItem()
  const deleteItem = usePlatformFeedbackDeleteItem()
  const [status, setStatus] = useState(item.status)
  const [title, setTitle] = useState(item.title)
  const [summary, setSummary] = useState(item.summary ?? '')
  const [resolutionSummary, setResolutionSummary] = useState(
    item.resolution_summary ?? defaultResolutionSummary(item),
  )
  const [notifyInApp, setNotifyInApp] = useState(true)
  const [notifyEmail, setNotifyEmail] = useState(false)
  const [resolveNotice, setResolveNotice] = useState<string | null>(null)
  const [resolveError, setResolveError] = useState<string | null>(null)
  const [copyNotice, setCopyNotice] = useState<string | null>(null)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [itemStep, setItemStep] = useState<'understand' | 'debug' | 'fix' | 'message'>(
    'understand',
  )
  const resolveLabel = feedbackResolveLabel(item.kind)
  const isClosed = CLOSED_FEEDBACK_ITEM_STATUSES.has(status)
  const debugInstructions = buildFeedbackDebugInstructions(item, submissions, fmtDate)
  const itemStepOrder: Array<'understand' | 'debug' | 'fix' | 'message'> =
    item.kind === 'bug'
      ? ['understand', 'debug', 'fix', 'message']
      : ['understand', 'fix', 'message']
  const itemStepIndex = Math.max(0, itemStepOrder.indexOf(itemStep))
  const itemWizardSteps: StepItem[] = itemStepOrder.map((step) => ({
    label:
      step === 'understand'
        ? m.platform_feedback_item_details()
        : step === 'debug'
          ? m.platform_feedback_copy_debug_title()
          : step === 'fix'
            ? m.platform_feedback_follow_up_title()
            : resolveLabel.title,
    onClick: () => setItemStep(step),
  }))
  const previousItemStep = () => {
    if (itemStepIndex > 0) setItemStep(itemStepOrder[itemStepIndex - 1])
  }
  const nextItemStep = () => {
    if (itemStepIndex < itemStepOrder.length - 1) {
      setItemStep(itemStepOrder[itemStepIndex + 1])
    }
  }
  const saveItem = () => {
    updateItem.mutate({
      itemId: item.id,
      status,
      title: title.trim(),
      summary: summary.trim() || null,
    })
  }
  const closeItem = () => {
    const channels: Array<'in_app' | 'email'> = []
    if (notifyInApp) channels.push('in_app')
    if (notifyEmail) channels.push('email')
    setResolveNotice(m.platform_feedback_update_creating())
    setResolveError(null)
    resolveItem.mutate(
      {
        itemId: item.id,
        resolution_summary: resolutionSummary.trim(),
        subject: `${resolveLabel.subject}: ${title.trim() || item.title}`,
        channels,
      },
      {
        onSuccess: (result) => {
          setStatus(result.item.status)
          setResolutionSummary(result.item.resolution_summary ?? '')
          setResolveNotice(
            m.platform_feedback_update_created({
              count: String(result.notifications.length),
            }),
          )
          setResolveError(null)
        },
        onError: (error) => {
          setResolveNotice(null)
          setResolveError(feedbackActionErrorMessage(error))
        },
      },
    )
  }
  const copyDebugInstructions = async () => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) {
      setCopyNotice(m.platform_feedback_copy_debug_failed())
      return
    }
    try {
      await navigator.clipboard.writeText(debugInstructions)
      setCopyNotice(m.platform_feedback_copy_debug_copied())
    } catch {
      setCopyNotice(m.platform_feedback_copy_debug_failed())
    }
  }

  return (
    <div className="space-y-8">
      <StepIndicator steps={itemWizardSteps} currentIndex={itemStepIndex} />

      {itemStep === 'understand' && (
        <>
      <section className="space-y-4 border-t border-gray-200 pt-6">
        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
          <Badge variant="outline">{feedbackItemKindLabel(item.kind)}</Badge>
          <Badge variant={isClosed ? 'secondary' : 'outline'}>
            {feedbackItemStatusLabel(status)}
          </Badge>
          <Badge variant="outline">
            {m.platform_feedback_org_count({ count: String(item.org_count) })}
          </Badge>
          <Badge variant="outline">
            {m.platform_feedback_user_count({ count: String(item.user_count) })}
          </Badge>
          <Badge variant="outline">
            {m.platform_feedback_score({ score: String(item.priority_score) })}
          </Badge>
          {item.area && <Badge variant="outline">{item.area}</Badge>}
        </div>
        <div>
          <h2 className="mt-1 text-xl font-display-bold text-gray-900">{title}</h2>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-700">
            {summary || m.platform_feedback_no_description()}
          </p>
        </div>
        <div className="grid gap-3 text-sm text-gray-600 sm:grid-cols-2 lg:grid-cols-4">
          <FeedbackMetaRow label={m.platform_col_status()} value={feedbackItemStatusLabel(status)} />
          <FeedbackMetaRow label={m.platform_col_created()} value={fmtDate(item.created_at)} />
          <FeedbackMetaRow label={m.platform_feedback_col_updated()} value={fmtDate(item.updated_at)} />
          <FeedbackMetaRow
            label={m.platform_feedback_item_signal()}
            value={m.platform_feedback_reporter_counts({
              orgs: String(item.org_count),
              users: String(item.user_count),
            })}
          />
        </div>
      </section>

      <section className="space-y-3 border-t border-gray-200 pt-6">
        <h3 className="text-sm font-medium text-gray-900">
          {m.platform_feedback_linked_feedback({ count: String(submissions.length) })}
        </h3>
        {submissions.length === 0 ? (
          <p className="rounded-lg border border-gray-200 px-4 py-3 text-sm text-gray-600">
            {m.platform_feedback_no_linked_feedback_warning()}
          </p>
        ) : (
          <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
            {submissions.map((submission) => (
              <div key={submission.id} className="py-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{feedbackKindLabel(submission.event_type)}</Badge>
                  <Badge variant="secondary">{submission.link_type}</Badge>
                  <span className="text-xs text-gray-400">{fmtDate(submission.created_at)}</span>
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-900">
                  {submission.raw_text}
                </p>
                <div className="mt-2 grid gap-2 text-xs text-gray-400 sm:grid-cols-2">
                  <span>
                    {submission.org_name ?? submission.org_slug ?? m.platform_feedback_unknown_organization()}
                    {feedbackSubmissionReporterLabel(submission)
                      ? ` / ${feedbackSubmissionReporterLabel(submission)}`
                      : ''}
                  </span>
                  <span>{submission.page_url || submission.route_id || '-'}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
        </>
      )}

      {item.kind === 'bug' && itemStep === 'debug' && (
        <section className="space-y-4 border-t border-gray-200 pt-6">
          <div>
            <h3 className="mt-1 text-base font-display-bold text-gray-900">
              {m.platform_feedback_copy_debug_title()}
            </h3>
            <p className="mt-1 text-sm leading-6 text-gray-600">
              {m.platform_feedback_copy_debug_description()}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={() => void copyDebugInstructions()}>
              <Copy className="h-4 w-4" />
              {m.platform_feedback_copy_debug_button()}
            </Button>
            {copyNotice && (
              <p className="text-sm text-[var(--color-success)]">
                {copyNotice}
              </p>
            )}
          </div>
        </section>
      )}

      {itemStep === 'fix' && (
        <section className="space-y-4 border-t border-gray-200 pt-6">
        <div>
          <h3 className="mt-1 text-base font-display-bold text-gray-900">
            {m.platform_feedback_follow_up_title()}
          </h3>
        </div>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor={`feedback-item-title-${item.id}`}>
                {m.platform_feedback_title_placeholder()}
              </Label>
              <Input
                id={`feedback-item-title-${item.id}`}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={m.platform_feedback_title_placeholder()}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`feedback-item-summary-${item.id}`}>
                {m.platform_feedback_short_note_placeholder()}
              </Label>
              <Textarea
                id={`feedback-item-summary-${item.id}`}
                value={summary}
                onChange={(event) => setSummary(event.target.value)}
                rows={4}
                placeholder={m.platform_feedback_short_note_placeholder()}
              />
            </div>
          </div>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor={`feedback-item-status-${item.id}`}>
                {m.platform_col_status()}
              </Label>
              <Select
                id={`feedback-item-status-${item.id}`}
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="open">{m.platform_feedback_status_open()}</option>
                <option value="resolved">{m.platform_feedback_status_resolved()}</option>
                <option value="dismissed">{m.platform_feedback_status_dismissed()}</option>
              </Select>
            </div>
            <Button
              type="button"
              className="w-full"
              disabled={updateItem.isPending || deleteItem.isPending || title.trim().length < 3}
              onClick={saveItem}
            >
              {updateItem.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {m.admin_shared_save()}
            </Button>
            <Button
              type="button"
              variant="secondary"
              className="w-full"
              disabled={updateItem.isPending || deleteItem.isPending}
              onClick={() => setConfirmDeleteOpen(true)}
            >
              {deleteItem.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              {m.platform_feedback_delete_item()}
            </Button>
          </div>
        </div>
        {updateItem.isSuccess && (
          <p className="text-sm text-[var(--color-success)]">
            {m.platform_feedback_item_saved()}
          </p>
        )}
      </section>
      )}

      {itemStep === 'message' && (
        <section className="space-y-4 border-t border-gray-200 pt-6">
        <div>
          <h3 className="mt-1 text-base font-display-bold text-gray-900">{resolveLabel.title}</h3>
          <p className="mt-1 text-sm leading-6 text-gray-600">
            {m.platform_feedback_resolve_description()}
          </p>
        </div>
        {isClosed && resolutionSummary && (
          <div className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900">
            {resolutionSummary}
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor={`feedback-item-resolution-${item.id}`}>
            {m.platform_feedback_resolution_placeholder()}
          </Label>
          <Textarea
            id={`feedback-item-resolution-${item.id}`}
            value={resolutionSummary}
            onChange={(event) => setResolutionSummary(event.target.value)}
            rows={3}
            placeholder={m.platform_feedback_resolution_placeholder()}
          />
        </div>
        <div className="flex flex-wrap items-center gap-5">
          <Checkbox
            checked={notifyInApp}
            onChange={(event) => setNotifyInApp(event.target.checked)}
            label={m.platform_feedback_channel_in_app()}
          />
          <Checkbox
            checked={notifyEmail}
            onChange={(event) => setNotifyEmail(event.target.checked)}
            label={m.platform_feedback_channel_email()}
          />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="secondary"
            disabled={
              resolveItem.isPending ||
              resolutionSummary.trim().length < 3 ||
              (!notifyInApp && !notifyEmail)
            }
            onClick={closeItem}
          >
            {resolveItem.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <CheckCircle2 className="h-4 w-4" />
            )}
            {resolveItem.isPending
              ? m.platform_feedback_resolving()
              : isClosed
                ? m.platform_feedback_resend_update()
                : resolveLabel.button}
          </Button>
          {resolveNotice && (
            <p className="text-sm text-[var(--color-success)]">
              {resolveNotice}
            </p>
          )}
          {resolveError && (
            <p className="text-sm text-[var(--color-destructive)]">
              {resolveError}
            </p>
          )}
        </div>
      </section>
      )}

      <div className="flex items-center justify-between border-t border-gray-200 pt-6">
        <Button
          type="button"
          variant="ghost"
          disabled={itemStepIndex === 0}
          onClick={previousItemStep}
        >
          <ArrowLeft className="h-4 w-4" />
          {m.admin_shared_wizard_previous()}
        </Button>
        {itemStepIndex < itemStepOrder.length - 1 && (
          <Button type="button" onClick={nextItemStep}>
            {m.admin_shared_wizard_next()}
            <ArrowRight className="h-4 w-4" />
          </Button>
        )}
      </div>
      <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.platform_feedback_delete_item_title()}</AlertDialogTitle>
            <AlertDialogDescription>
              {m.platform_feedback_delete_item_description()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                deleteItem.mutate(item.id, { onSuccess: onClose })
              }}
            >
              {m.platform_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function feedbackResolveLabel(kind: string) {
  if (kind === 'bug') {
    return {
      title: m.platform_feedback_resolve_bug_title(),
      button: m.platform_feedback_resolve_bug_button(),
      subject: m.platform_feedback_resolve_bug_subject(),
    }
  }
  if (kind === 'feature') {
    return {
      title: m.platform_feedback_resolve_feature_title(),
      button: m.platform_feedback_resolve_feature_button(),
      subject: m.platform_feedback_resolve_feature_subject(),
    }
  }
  return {
    title: m.platform_feedback_resolve_report_title(),
    button: m.platform_feedback_resolve_report_button(),
    subject: m.platform_feedback_resolve_report_subject(),
  }
}

function feedbackActionErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return m.platform_feedback_action_failed()
}

function defaultResolutionSummary(item: PlatformFeedbackItem) {
  if (item.kind === 'bug') {
    return m.platform_feedback_default_resolution_bug({ title: item.title })
  }
  if (item.kind === 'feature') {
    return m.platform_feedback_default_resolution_feature({ title: item.title })
  }
  return m.platform_feedback_default_resolution_report({ title: item.title })
}

function FeedbackMetaRow({
  label,
  value,
}: {
  label: string
  value: ReactNode
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-gray-400">{label}</p>
      <div className="text-sm text-gray-900">{value || '-'}</div>
    </div>
  )
}

function buildFeedbackDebugInstructions(
  item: PlatformFeedbackItem,
  submissions: PlatformFeedbackLinkedSubmission[],
  fmtDate: (s: string | null) => string,
) {
  const evidence = submissions.length
    ? submissions
        .map((submission, index) =>
          [
            `${index + 1}. ${submission.raw_text || '(empty)'}`,
            `   Org: ${submission.org_name ?? submission.org_slug ?? 'unknown'}`,
            `   Reporter: ${feedbackSubmissionReporterLabel(submission) || submission.user_id || 'unknown'}`,
            `   URL: ${submission.page_url || 'unknown'}`,
            `   Route: ${submission.route_id || 'unknown'}`,
            `   Locale/viewport: ${[submission.locale, submission.viewport].filter(Boolean).join(' / ') || 'unknown'}`,
            `   Submitted: ${fmtDate(submission.created_at)}`,
          ].join('\n'),
        )
        .join('\n\n')
    : 'No linked feedback evidence yet.'

  return [
    'You are fixing a Klai production bug from the Platform feedback workflow.',
    '',
    'Goal:',
    `Fix the bug item #${item.id}: ${item.title}`,
    '',
    'Current item state:',
    `- Kind: ${item.kind}`,
    `- Status: ${item.status}`,
    `- Area: ${item.area || 'unknown'}`,
    `- Priority score: ${item.priority_score}`,
    `- Reporter signal: ${item.org_count} org(s), ${item.user_count} user(s)`,
    '',
    'Internal note:',
    item.summary || '(empty)',
    '',
    'Linked customer evidence:',
    evidence,
    '',
    'Instructions:',
    '1. Reproduce or trace the issue from the linked URL, route, and raw customer text.',
    '2. Identify the smallest backend/frontend change that fixes the actual cause.',
    '3. Add or update focused regression coverage for the broken behavior.',
    '4. Verify the delete/save/close path still works if this bug touches Platform feedback.',
    '5. Report changed files, tests run, and any residual risk.',
  ].join('\n')
}
