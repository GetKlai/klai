import { useNavigate } from "@tanstack/react-router"
import { Activity, Bug, ExternalLink, Loader2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import * as m from "@/paraglide/messages"
import {
  usePlatformBots,
  usePlatformChatErrors,
  usePlatformKnowledgeBases,
  usePlatformOrgs,
  usePlatformSubdomains,
  usePlatformTemplates,
  usePlatformUsers,
  usePortalHealth,
} from "../-hooks"
import type { PlatformSubdomainItem } from "../-types"
import { PlatformTableShell, TD, TH } from "./PlatformShell"

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

