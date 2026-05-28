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
  Pencil,
  PlusCircle,
  Save,
  Search,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
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
  usePlatformFeedbackDeleteItem,
  usePlatformFeedbackDismiss,
  usePlatformFeedbackItem,
  usePlatformFeedbackItems,
  usePlatformFeedbackLinkItem,
  usePlatformFeedbackSubmissions,
  usePlatformFeedbackSupport,
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

function feedbackStatusLabel(status: string): string {
  if (status === 'linked') return 'Gekoppeld'
  if (status === 'dismissed') return 'Genegeerd'
  if (status === 'support') return 'Support'
  if (status === 'triage_suggested') return 'AI voorstel'
  return 'Nieuw'
}

function feedbackItemStatusLabel(status: string): string {
  if (status === 'under_review') return 'In review'
  if (status === 'planned') return 'Gepland'
  if (status === 'in_progress') return 'In uitvoering'
  if (status === 'shipped') return 'Verzonden'
  if (status === 'wont_do') return "Won't do"
  return 'Inbox'
}

function feedbackItemKindLabel(kind: string): string {
  if (kind === 'bug') return 'Bug'
  if (kind === 'ux_confusion') return 'UX'
  if (kind === 'docs') return 'Docs'
  if (kind === 'support_pattern') return 'Support'
  return 'Feature'
}

function feedbackSuggestionActionLabel(action: string | null | undefined): string {
  if (action === 'link_existing') return 'Link met bestaand item'
  if (action === 'create_item') return 'Maak nieuw item'
  if (action === 'support') return 'Support'
  if (action === 'dismiss') return 'Negeer'
  return 'Bekijk handmatig'
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
    return shortTitle ? `Koppel aan ${shortTitle}` : 'Koppel aan bestaand item'
  }
  if (action === 'support') return 'Markeer als support'
  if (action === 'dismiss') return 'Negeer melding'
  return `Maak ${feedbackItemKindLabel(kind).toLowerCase()} item`
}

function feedbackFallbackSummary(item: PlatformFeedbackSubmission): string {
  if (item.event_type === 'klai_assistant.problem_report') {
    return `Bugmelding: ${item.raw_text || 'Geen omschrijving.'}`
  }
  return item.raw_text || 'Geen omschrijving.'
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
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedItemId, setSelectedItemId] = useState<number | null>(null)
  const [feedbackView, setFeedbackView] = useState<'inbox' | 'items'>('inbox')
  const rows = data ?? []
  const selected = rows.find((row) => row.id === selectedId) ?? null

  return (
    <>
      <div className="mb-5 inline-flex rounded-lg border border-gray-200 bg-white p-1">
        <Button
          type="button"
          size="sm"
          variant={feedbackView === 'inbox' ? 'default' : 'ghost'}
          onClick={() => setFeedbackView('inbox')}
        >
          Inbox
        </Button>
        <Button
          type="button"
          size="sm"
          variant={feedbackView === 'items' ? 'default' : 'ghost'}
          onClick={() => setFeedbackView('items')}
        >
          Roadmap items
        </Button>
      </div>

      {feedbackView === 'items' ? (
        <RoadmapItemsPanel
          search={search}
          fmtDate={fmtDate}
          onOpenItem={setSelectedItemId}
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
              <th className={TH}>Status</th>
              <th className={TH}>{m.platform_col_organization()}</th>
              <th className={TH}>{m.platform_col_detail()}</th>
              <th className={TH}>{m.platform_feedback_context()}</th>
              <th className={TH}>{m.platform_col_time()}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <FeedbackSubmissionRow
                key={item.id}
                item={item}
                fmtDate={fmtDate}
                onOpen={() => setSelectedId(item.id)}
              />
            ))}
          </tbody>
        </PlatformTableShell>
      )}

      {selected && (
        <FeedbackDetailSheet
          key={selected.id}
          item={selected}
          fmtDate={fmtDate}
          onClose={() => setSelectedId(null)}
        />
      )}
      {selectedItemId !== null && (
        <FeedbackItemDetailSheet
          itemId={selectedItemId}
          fmtDate={fmtDate}
          onClose={() => setSelectedItemId(null)}
        />
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
      className="cursor-pointer border-b border-gray-200 transition-colors last:border-b-0 hover:bg-gray-50"
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
  )
}

function RoadmapItemsPanel({
  search,
  fmtDate,
  onOpenItem,
}: {
  search: string
  fmtDate: (s: string | null) => string
  onOpenItem: (itemId: number) => void
}) {
  const items = usePlatformFeedbackItems(search)
  const deleteItem = usePlatformFeedbackDeleteItem()
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)
  const rows = items.data ?? []

  return (
    <section className="mb-6 space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-gray-900">Roadmap items</h2>
          <p className="mt-0.5 text-xs text-gray-400">
            Gebundelde feedback. Open een item om te bewerken of verwijderen.
          </p>
        </div>
        {items.isFetching && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
      </div>
      {rows.length === 0 ? (
        <p className="rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-500">
          Nog geen feedback items gevonden.
        </p>
      ) : (
        <PlatformTableShell loading={false} empty={false} emptyText="">
          <thead>
            <tr>
              <th className={TH}>Item</th>
              <th className={TH}>Status</th>
              <th className={TH}>Type</th>
              <th className={TH}>Impact</th>
              <th className={TH}>Bijgewerkt</th>
              <th className={`${TH} text-right`}>Acties</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {rows.map((item) => {
              const isConfirming = confirmDeleteId === item.id
              return (
                <tr
                  key={item.id}
                  className={isConfirming ? 'bg-[var(--color-hover)]' : 'hover:bg-gray-50'}
                >
                  <td className={`${TD} max-w-xl`}>
                    <button
                      type="button"
                      className="block max-w-full text-left"
                      onClick={() => onOpenItem(item.id)}
                    >
                      <span className="block truncate font-medium text-gray-900">
                        {item.title}
                      </span>
                      <span className="mt-1 block truncate text-xs text-gray-400">
                        {[item.area, item.owner && `owner: ${item.owner}`]
                          .filter(Boolean)
                          .join(' / ')}
                      </span>
                    </button>
                  </td>
                  <td className={TD}>
                    <Badge variant="outline">{feedbackItemStatusLabel(item.status)}</Badge>
                  </td>
                  <td className={TD}>{feedbackItemKindLabel(item.kind)}</td>
                  <td className={`${TD} whitespace-nowrap tabular-nums`}>
                    {item.org_count} orgs / {item.user_count} users
                  </td>
                  <td className={`${TD} whitespace-nowrap text-gray-400`}>
                    {fmtDate(item.updated_at)}
                  </td>
                  <td className={`${TD} text-right`}>
                    <InlineDeleteConfirm
                      isConfirming={isConfirming}
                      isPending={deleteItem.isPending && isConfirming}
                      label="Verwijder"
                      cancelLabel={m.admin_users_cancel()}
                      onConfirm={() => {
                        deleteItem.mutate(item.id, {
                          onSuccess: () => setConfirmDeleteId(null),
                        })
                      }}
                      onCancel={() => setConfirmDeleteId(null)}
                    >
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 rounded-md text-gray-400 hover:text-gray-900"
                          aria-label={`Bewerk ${item.title}`}
                          title="Bewerken"
                          onClick={() => onOpenItem(item.id)}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 rounded-md text-gray-400 hover:text-[var(--color-destructive)]"
                          aria-label={`Verwijder ${item.title}`}
                          title="Verwijderen"
                          onClick={() => setConfirmDeleteId(item.id)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </InlineDeleteConfirm>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </PlatformTableShell>
      )}
    </section>
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
  const suggestion = item.triage_suggestion
  const bestCandidate = suggestion?.duplicate_candidates[0] ?? null
  const suggestedKind = normalizedFeedbackKind(suggestion?.classification, defaultKind)
  const suggestedTitle = (suggestion?.summary || item.raw_text || '').slice(0, 90)
  const suggestedSearch = (
    bestCandidate?.title ||
    [suggestion?.summary, suggestion?.suggested_area].filter(Boolean).join(' ') ||
    item.raw_text ||
    ''
  ).slice(0, 80)
  const [showCorrections, setShowCorrections] = useState(false)
  const [itemSearch, setItemSearch] = useState(suggestedSearch)
  const [kind, setKind] = useState(suggestedKind)
  const [title, setTitle] = useState(suggestedTitle)
  const [summary, setSummary] = useState(suggestion?.summary ?? item.raw_text ?? '')
  const [area, setArea] = useState(suggestion?.suggested_area ?? '')

  const items = usePlatformFeedbackItems(itemSearch)
  const existingItems = items.data ?? []
  const bestSearchMatch = itemSearch.trim().length >= 4 ? (existingItems[0] ?? null) : null
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
  const linkType =
    item.event_type === 'klai_assistant.problem_report'
      ? 'bug_repro'
      : suggestion?.classification === 'support_pattern'
        ? 'support_signal'
        : 'evidence'
  const recommendedAction =
    bestCandidate || bestSearchMatch
      ? 'link_existing'
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
  const proposalSummary = suggestion?.summary || feedbackFallbackSummary(item)
  const acceptCreateItem = () => {
    const fallbackTitle = (suggestion?.summary || item.raw_text || '').slice(0, 90)
    createItem.mutate(
      {
        submissionId: item.id,
        kind: suggestedKind,
        title: fallbackTitle.trim(),
        summary: item.raw_text || suggestion?.summary || null,
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
    (recommendedAction !== 'create_item' || suggestedTitle.trim().length >= 3)

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
          </section>

          {canTriage ? (
            <>
              <section className="space-y-3 rounded-lg border border-amber-200 bg-amber-50 p-4">
                <div className="flex min-w-0 items-start gap-2">
                  <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                  <div className="min-w-0">
                    <h3 className="text-sm font-medium text-gray-900">
                      {suggestion ? 'AI voorstel' : 'Voorstel'}
                    </h3>
                    <p className="mt-1 text-sm leading-6 text-gray-700">
                      {proposalSummary}
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                  <Badge variant="outline">
                    Actie: {feedbackSuggestionActionLabel(recommendedAction)}
                  </Badge>
                  <Badge variant="outline">
                    Type: {feedbackItemKindLabel(suggestedKind)}
                  </Badge>
                  {suggestion?.suggested_area && (
                    <Badge variant="outline">Gebied: {suggestion.suggested_area}</Badge>
                  )}
                  {suggestion?.suggested_severity && (
                    <Badge variant="outline">Urgentie: {suggestion.suggested_severity}</Badge>
                  )}
                </div>
                {recommendedItem && (
                  <div className="rounded-md border border-amber-200 bg-white px-3 py-2">
                    <p className="text-xs font-medium text-gray-500">
                      Bestaand item gevonden
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
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    disabled={busy || !canAcceptRecommendedAction || items.isFetching}
                    onClick={acceptRecommendedAction}
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    {feedbackSuggestionPrimaryLabel(
                      recommendedAction,
                      recommendedItem?.title,
                      suggestedKind,
                    )}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => setShowCorrections((visible) => !visible)}
                  >
                    Andere actie
                  </Button>
                </div>
              </section>

              {showCorrections && (
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
                    Markeer als support
                  </Button>
                  <p className="text-xs leading-5 text-gray-500 sm:col-span-2">
                    Support betekent: geen product- of roadmap-item maken; de melding
                    wordt afgehandeld als klantvraag/supportsignaal.
                  </p>
                </section>
              )}

              {showCorrections && (
                <section className="space-y-3 border-t border-gray-200 pt-5">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-medium text-gray-900">Koppel aan bestaand item</h3>
                    {items.isFetching && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
                  </div>
                  <label className="block space-y-1">
                    <span className="text-xs font-medium text-gray-500">
                      Slimme zoekterm voor roadmap items
                    </span>
                    <span className="relative block">
                      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                      <Input
                        value={itemSearch}
                        onChange={(event) => setItemSearch(event.target.value)}
                        placeholder="Titel of onderwerp"
                        className="pl-9"
                      />
                    </span>
                  </label>
                  {suggestion?.duplicate_candidates.length ? (
                    <p className="text-xs text-gray-500">
                      AI heeft al mogelijke matches hierboven getoond. Gebruik zoeken
                      alleen als je een ander item wilt koppelen.
                    </p>
                  ) : null}
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
                                link_type: linkType,
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
                    {!items.isFetching && (items.data ?? []).length === 0 && (
                      <p className="rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-500">
                        Geen bestaand roadmap item gevonden.
                      </p>
                    )}
                  </div>
                </section>
              )}

              {showCorrections && (
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
                          link_type: linkType,
                        },
                        { onSuccess: onClose },
                      )
                    }}
                  >
                    <PlusCircle className="h-4 w-4" />
                    Maak item
                  </Button>
                </section>
              )}
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

function FeedbackItemDetailSheet({
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
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Roadmap item</SheetTitle>
          <SheetDescription>
            Source of truth voor gebundelde feedback en klantupdates.
          </SheetDescription>
        </SheetHeader>

        {detail.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Laden...
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
          <p className="text-sm text-gray-500">Item niet gevonden.</p>
        )}
      </SheetContent>
    </Sheet>
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
  const deleteItem = usePlatformFeedbackDeleteItem()
  const [status, setStatus] = useState(item.status)
  const [title, setTitle] = useState(item.title)
  const [summary, setSummary] = useState(item.summary ?? '')
  const saveItem = () => {
    updateItem.mutate({
      itemId: item.id,
      status,
      title: title.trim(),
      summary: summary.trim() || null,
    })
  }

  return (
    <div className="space-y-5">
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
          <Badge variant="outline">{feedbackItemKindLabel(item.kind)}</Badge>
          <Badge variant="outline">{item.org_count} orgs</Badge>
          <Badge variant="outline">{item.user_count} users</Badge>
          <Badge variant="secondary">score {item.priority_score}</Badge>
          {item.area && <Badge variant="outline">{item.area}</Badge>}
        </div>
        <div className="grid gap-3">
          <Select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="inbox">Inbox</option>
            <option value="under_review">In review</option>
            <option value="planned">Gepland</option>
            <option value="in_progress">In uitvoering</option>
            <option value="shipped">Verzonden</option>
            <option value="wont_do">Won't do</option>
          </Select>
        </div>
        <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Titel" />
        <Textarea
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          rows={3}
          placeholder="Korte notitie"
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            disabled={updateItem.isPending || deleteItem.isPending || title.trim().length < 3}
            onClick={saveItem}
          >
            {updateItem.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Opslaan
          </Button>
          {updateItem.isSuccess && (
            <p className="text-sm text-green-700">Roadmap item opgeslagen.</p>
          )}
          <Button
            type="button"
            variant="secondary"
            disabled={updateItem.isPending || deleteItem.isPending}
            onClick={() => {
              if (!window.confirm('Roadmap item verwijderen en gekoppelde feedback terugzetten naar nieuw?')) {
                return
              }
              deleteItem.mutate(item.id, { onSuccess: onClose })
            }}
          >
            {deleteItem.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Trash2 className="h-4 w-4" />
            )}
            Verwijder item
          </Button>
        </div>
      </section>

      <section className="space-y-3 border-t border-gray-200 pt-5">
        <h3 className="text-sm font-medium text-gray-900">
          Gekoppelde feedback ({submissions.length})
        </h3>
        <div className="space-y-2">
          {submissions.map((submission) => (
            <div key={submission.id} className="rounded-lg border border-gray-200 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{feedbackKindLabel(submission.event_type)}</Badge>
                <Badge variant="secondary">{submission.link_type}</Badge>
                <span className="text-xs text-gray-400">{fmtDate(submission.created_at)}</span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-900">
                {submission.raw_text}
              </p>
              <p className="mt-2 text-xs text-gray-400">
                {submission.org_name ?? submission.org_slug ?? 'Onbekende org'}
                {submission.user_id ? ` / ${submission.user_id}` : ''}
              </p>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
