import { useNavigate } from "@tanstack/react-router"
import { Activity, Bug, ExternalLink } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from "@/components/ui/data-table"
import { ListEmptyState, ListLoadingState } from "@/components/ui/list-state"
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
        <ListLoadingState label={m.platform_subdomains_loading()} />
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
            <DataTable>
              <DataTableHeader>
                <DataTableRow>
                  <DataTableHead>{m.platform_subdomains_col_subdomain()}</DataTableHead>
                  <DataTableHead>{m.platform_subdomains_col_label()}</DataTableHead>
                  <DataTableHead>{m.platform_subdomains_col_host()}</DataTableHead>
                  <DataTableHead>{m.platform_subdomains_col_owner()}</DataTableHead>
                  <DataTableHead>{m.platform_subdomains_col_status()}</DataTableHead>
                  <DataTableHead />
                </DataTableRow>
              </DataTableHeader>
              <DataTableBody>
                {rows.map((item) => (
                  <DataTableRow key={item.url}>
                    <DataTableCell>
                      <p className="font-mono text-xs text-gray-900">{item.subdomain || '(apex)'}</p>
                      <p className="mt-1 text-xs text-gray-400">{item.description}</p>
                    </DataTableCell>
                    <DataTableCell>
                      <span className="text-sm">{item.label}</span>
                    </DataTableCell>
                    <DataTableCell>
                      <span className="text-xs font-mono text-gray-400">{item.host}</span>
                    </DataTableCell>
                    <DataTableCell>
                      <span className="text-sm text-gray-700">{item.owner}</span>
                    </DataTableCell>
                    <DataTableCell>
                      <SubdomainStatusBadge item={item} />
                    </DataTableCell>
                    <DataTableCell align="right">
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 text-sm text-[var(--color-rl-accent-dark)] hover:underline"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
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
            <ListLoadingState label={m.platform_checking()} className="py-0" />
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

  return isLoading ? (
    <ListLoadingState label={m.admin_shared_loading()} />
  ) : rows.length === 0 ? (
    <ListEmptyState title={m.platform_empty_users()} />
  ) : (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.platform_col_user()}</DataTableHead>
          <DataTableHead>{m.platform_col_organization()}</DataTableHead>
          <DataTableHead>{m.platform_col_plan()}</DataTableHead>
          <DataTableHead>{m.platform_col_created()}</DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {rows.map((u) => (
          <DataTableRow
            key={u.zitadel_user_id}
            interactive
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(u.org_id) },
              })
            }
          >
            <DataTableCell>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">
                  {u.display_name || u.email || u.zitadel_user_id}
                </span>
                {u.is_admin && (
                  <Badge variant="secondary">{m.platform_admin()}</Badge>
                )}
              </div>
              {u.email && <p className="text-xs text-gray-400">{u.email}</p>}
            </DataTableCell>
            <DataTableCell>
              <div className="flex flex-wrap items-center gap-2">
                <span>{u.org_name}</span>
                {!u.org_onboarded && (
                  <Badge variant="outline">{m.platform_not_onboarded()}</Badge>
                )}
              </div>
            </DataTableCell>
            <DataTableCell>
              <Badge variant="outline">{u.org_plan}</Badge>
            </DataTableCell>
            <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
              {fmtDate(u.created_at)}
            </DataTableCell>
          </DataTableRow>
        ))}
      </DataTableBody>
    </DataTable>
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

  return isLoading ? (
    <ListLoadingState label={m.admin_shared_loading()} />
  ) : rows.length === 0 ? (
    <ListEmptyState title={m.platform_empty_organizations()} />
  ) : (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.platform_col_organization()}</DataTableHead>
          <DataTableHead>{m.platform_col_plan()}</DataTableHead>
          <DataTableHead>{m.platform_col_users()}</DataTableHead>
          <DataTableHead>{m.platform_col_bots()}</DataTableHead>
          <DataTableHead>{m.platform_col_kbs()}</DataTableHead>
          <DataTableHead>{m.platform_col_status()}</DataTableHead>
          <DataTableHead>{m.platform_col_created()}</DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {rows.map((o) => (
          <DataTableRow
            key={o.id}
            interactive
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(o.id) },
              })
            }
          >
            <DataTableCell>
              <span className="font-medium">{o.name}</span>
              <p className="font-mono text-xs text-gray-400">{o.slug}</p>
            </DataTableCell>
            <DataTableCell>
              <Badge variant="outline">{o.plan}</Badge>
            </DataTableCell>
            <DataTableCell className="tabular-nums">{o.user_count}</DataTableCell>
            <DataTableCell className="tabular-nums">{o.bot_count}</DataTableCell>
            <DataTableCell className="tabular-nums">{o.kb_count}</DataTableCell>
            <DataTableCell>
              <Badge
                variant={
                  o.provisioning_status === 'ready' ? 'success' : 'outline'
                }
              >
                {o.provisioning_status}
              </Badge>
            </DataTableCell>
            <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
              {fmtDate(o.created_at)}
            </DataTableCell>
          </DataTableRow>
        ))}
      </DataTableBody>
    </DataTable>
  )
}

export function SubsTab({ search }: { search: string }) {
  const { data, isLoading } = usePlatformOrgs(search)
  const rows = data ?? []

  return isLoading ? (
    <ListLoadingState label={m.admin_shared_loading()} />
  ) : rows.length === 0 ? (
    <ListEmptyState title={m.platform_empty_subscriptions()} />
  ) : (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.platform_col_organization()}</DataTableHead>
          <DataTableHead>{m.platform_col_plan()}</DataTableHead>
          <DataTableHead>{m.platform_col_cycle()}</DataTableHead>
          <DataTableHead>{m.platform_col_seats()}</DataTableHead>
          <DataTableHead>{m.platform_col_billing_status()}</DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {rows.map((o) => (
          <DataTableRow key={o.id}>
            <DataTableCell>
              <span className="font-medium">{o.name}</span>
            </DataTableCell>
            <DataTableCell>
              <Badge variant="outline">{o.plan}</Badge>
            </DataTableCell>
            <DataTableCell>{o.billing_cycle}</DataTableCell>
            <DataTableCell className="tabular-nums">{o.seats}</DataTableCell>
            <DataTableCell>
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
            </DataTableCell>
          </DataTableRow>
        ))}
      </DataTableBody>
    </DataTable>
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

  return isLoading ? (
    <ListLoadingState label={m.admin_shared_loading()} />
  ) : rows.length === 0 ? (
    <ListEmptyState title={m.platform_empty_knowledge_bases()} />
  ) : (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.platform_col_knowledge_base()}</DataTableHead>
          <DataTableHead>{m.platform_col_organization()}</DataTableHead>
          <DataTableHead>{m.platform_col_type()}</DataTableHead>
          <DataTableHead>{m.platform_col_visibility()}</DataTableHead>
          <DataTableHead>{m.platform_col_created()}</DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {rows.map((kb) => (
          <DataTableRow
            key={kb.id}
            interactive
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(kb.org_id) },
              })
            }
          >
            <DataTableCell>
              <span className="font-medium">{kb.name}</span>
              <p className="font-mono text-xs text-gray-400">{kb.slug}</p>
            </DataTableCell>
            <DataTableCell>{kb.org_name}</DataTableCell>
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

  return isLoading ? (
    <ListLoadingState label={m.admin_shared_loading()} />
  ) : rows.length === 0 ? (
    <ListEmptyState title={m.platform_empty_templates()} />
  ) : (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.platform_col_template()}</DataTableHead>
          <DataTableHead>{m.platform_col_organization()}</DataTableHead>
          <DataTableHead>{m.platform_col_scope()}</DataTableHead>
          <DataTableHead>{m.platform_col_created_by()}</DataTableHead>
          <DataTableHead>{m.platform_col_created()}</DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {rows.map((t) => (
          <DataTableRow
            key={t.id}
            interactive
            onClick={() =>
              void navigate({
                to: '/admin/platform/orgs/$orgId',
                params: { orgId: String(t.org_id) },
              })
            }
          >
            <DataTableCell>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{t.name}</span>
                {!t.is_active && (
                  <Badge variant="outline">{m.platform_inactive()}</Badge>
                )}
              </div>
              <p className="font-mono text-xs text-gray-400">{t.slug}</p>
            </DataTableCell>
            <DataTableCell>{t.org_name}</DataTableCell>
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

  return isLoading ? (
    <ListLoadingState label={m.admin_shared_loading()} />
  ) : rows.length === 0 ? (
    <ListEmptyState title={m.platform_empty_bots()} />
  ) : (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.platform_col_bot()}</DataTableHead>
          <DataTableHead>{m.platform_col_organization()}</DataTableHead>
          <DataTableHead>{m.platform_col_knowledge_bases()}</DataTableHead>
          <DataTableHead>{m.platform_col_created()}</DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {rows.map((b) => (
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
            <DataTableCell>{b.org_name}</DataTableCell>
            <DataTableCell className="tabular-nums">{b.kb_count}</DataTableCell>
            <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
              {fmtDate(b.created_at)}
            </DataTableCell>
          </DataTableRow>
        ))}
      </DataTableBody>
    </DataTable>
  )
}

export function ChatErrorsTab({
  fmtDate,
}: {
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformChatErrors()
  const rows = data ?? []

  return isLoading ? (
    <ListLoadingState label={m.admin_shared_loading()} />
  ) : rows.length === 0 ? (
    <ListEmptyState title={m.platform_empty_chat_errors()} />
  ) : (
    <DataTable>
      <DataTableHeader>
        <DataTableRow>
          <DataTableHead>{m.platform_col_type()}</DataTableHead>
          <DataTableHead>{m.platform_col_organization()}</DataTableHead>
          <DataTableHead>{m.platform_col_detail()}</DataTableHead>
          <DataTableHead>{m.platform_col_time()}</DataTableHead>
        </DataTableRow>
      </DataTableHeader>
      <DataTableBody>
        {rows.map((e) => (
          <DataTableRow key={e.id}>
            <DataTableCell>
              <Badge variant="destructive">{e.event_type}</Badge>
            </DataTableCell>
            <DataTableCell>{e.org_name ?? `#${e.org_id}`}</DataTableCell>
            <DataTableCell className="max-w-md truncate text-gray-400">
              {e.detail ?? '-'}
            </DataTableCell>
            <DataTableCell className="whitespace-nowrap tabular-nums text-gray-400">
              {fmtDate(e.created_at)}
            </DataTableCell>
          </DataTableRow>
        ))}
      </DataTableBody>
    </DataTable>
  )
}
