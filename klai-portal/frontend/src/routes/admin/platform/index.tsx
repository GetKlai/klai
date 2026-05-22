import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Loader2, Plus, RotateCw, Search } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import {
  usePlatformStats,
  usePlatformUsers,
  usePlatformOrgs,
  usePlatformBots,
  usePlatformChatErrors,
  usePlatformKnowledgeBases,
} from './-hooks'
import type { PlatformTab } from './-types'

export const Route = createFileRoute('/admin/platform/')({
  component: PlatformConsole,
})

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

const TABS: { id: PlatformTab; label: string }[] = [
  { id: 'users', label: 'Gebruikers' },
  { id: 'organizations', label: 'Organisaties' },
  { id: 'knowledge-bases', label: 'Kennisbanken' },
  { id: 'subscriptions', label: 'Abonnementen' },
  { id: 'bots', label: 'Bots' },
  { id: 'chat-errors', label: 'Chat errors' },
]

function PlatformConsole() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [tab, setTab] = useState<PlatformTab>('users')
  const [search, setSearch] = useState('')

  const statsQuery = usePlatformStats()
  const stats = statsQuery.data

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['platform-stats'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-users'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-orgs'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-bots'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-chat-errors'] })
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            Platform
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Beheer gebruikers, organisaties en bots over alle tenants. Alleen
            zichtbaar voor Klai-staff.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void navigate({ to: '/admin/platform/new' })}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-gray-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-gray-800"
        >
          <Plus className="h-4 w-4" />
          Nieuwe tenant
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        <StatCard
          label="Gebruikers"
          value={stats?.total_users}
          sub={
            stats ? `+${stats.new_users_this_month} deze maand` : undefined
          }
          loading={statsQuery.isLoading}
        />
        <StatCard
          label="Organisaties"
          value={stats?.total_orgs}
          sub={
            stats
              ? `${stats.active_subscriptions} actief abonnement`
              : undefined
          }
          loading={statsQuery.isLoading}
        />
        <StatCard
          label="Bots"
          value={stats?.total_bots}
          sub={stats ? `+${stats.new_bots_today} vandaag` : undefined}
          loading={statsQuery.isLoading}
        />
        <StatCard
          label="Kennisbanken"
          value={stats?.total_kbs}
          loading={statsQuery.isLoading}
        />
        <StatCard
          label="MRR"
          value={stats ? `€${(stats.mrr_cents / 100).toFixed(2)}` : undefined}
          sub={
            stats ? `€${(stats.arr_cents / 100).toFixed(0)} ARR` : undefined
          }
          loading={statsQuery.isLoading}
        />
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex gap-6 overflow-x-auto">
          {TABS.map((t) => {
            const active = t.id === tab
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={[
                  'whitespace-nowrap pb-3 text-sm font-medium border-b-2 transition-colors',
                  active
                    ? 'border-gray-900 text-gray-900'
                    : 'border-transparent text-gray-400 hover:text-gray-900',
                ].join(' ')}
              >
                {t.label}
              </button>
            )
          })}
        </nav>
      </div>

      {/* Search + refresh */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Zoek op naam of email…"
            className="pl-9"
          />
        </div>
        <button
          type="button"
          onClick={refresh}
          className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 klai-hover"
        >
          <RotateCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Tab content */}
      {tab === 'users' && <UsersTab search={search} fmtDate={fmtDate} />}
      {tab === 'organizations' && (
        <OrgsTab search={search} fmtDate={fmtDate} />
      )}
      {tab === 'subscriptions' && <SubsTab search={search} />}
      {tab === 'knowledge-bases' && <KbTab search={search} fmtDate={fmtDate} />}
      {tab === 'bots' && <BotsTab search={search} fmtDate={fmtDate} />}
      {tab === 'chat-errors' && <ChatErrorsTab fmtDate={fmtDate} />}
    </div>
  )
}

function StatCard({
  label,
  value,
  sub,
  loading,
}: {
  label: string
  value: number | string | undefined
  sub?: string
  loading: boolean
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400">
        {label}
      </p>
      <p className="mt-1 text-3xl font-display-bold text-gray-900 tabular-nums">
        {loading ? (
          <Loader2 className="inline h-5 w-5 animate-spin text-gray-400" />
        ) : value === undefined ? (
          '—'
        ) : (
          value
        )}
      </p>
      {sub && <p className="mt-1 text-xs text-gray-400">{sub}</p>}
    </div>
  )
}

function TableShell({
  loading,
  empty,
  emptyText,
  children,
}: {
  loading: boolean
  empty: boolean
  emptyText: string
  children: React.ReactNode
}) {
  if (loading) {
    return (
      <p className="py-8 text-sm text-gray-400">
        <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
        Laden…
      </p>
    )
  }
  if (empty) {
    return <p className="py-8 text-sm text-gray-400">{emptyText}</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-t border-b border-gray-200">
        {children}
      </table>
    </div>
  )
}

const TH =
  'py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide whitespace-nowrap'
const TD = 'py-3.5 pr-4 align-top text-gray-900'

function UsersTab({
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
    <TableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText="Geen gebruikers gevonden."
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>Gebruiker</th>
          <th className={TH}>Organisatie</th>
          <th className={TH}>Plan</th>
          <th className={TH}>Aangemaakt</th>
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
            className="border-b border-gray-200 last:border-b-0 cursor-pointer klai-hover"
          >
            <td className={TD}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium">
                  {u.display_name || u.email || u.zitadel_user_id}
                </span>
                {u.is_admin && <Badge variant="secondary">Admin</Badge>}
              </div>
              {u.email && (
                <p className="text-xs text-gray-400">{u.email}</p>
              )}
            </td>
            <td className={TD}>
              <div className="flex items-center gap-2 flex-wrap">
                <span>{u.org_name}</span>
                {!u.org_onboarded && (
                  <Badge variant="outline">Niet onboarded</Badge>
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
    </TableShell>
  )
}

function OrgsTab({
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
    <TableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText="Geen organisaties gevonden."
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>Organisatie</th>
          <th className={TH}>Plan</th>
          <th className={TH}>Gebruikers</th>
          <th className={TH}>Bots</th>
          <th className={TH}>KB's</th>
          <th className={TH}>Status</th>
          <th className={TH}>Aangemaakt</th>
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
            className="border-b border-gray-200 last:border-b-0 cursor-pointer klai-hover"
          >
            <td className={TD}>
              <span className="font-medium">{o.name}</span>
              <p className="text-xs text-gray-400 font-mono">{o.slug}</p>
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
                  o.provisioning_status === 'complete'
                    ? 'success'
                    : 'outline'
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
    </TableShell>
  )
}

function SubsTab({ search }: { search: string }) {
  const { data, isLoading } = usePlatformOrgs(search)
  const rows = data ?? []
  return (
    <TableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText="Geen abonnementen gevonden."
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>Organisatie</th>
          <th className={TH}>Plan</th>
          <th className={TH}>Cyclus</th>
          <th className={TH}>Seats</th>
          <th className={TH}>Billing-status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((o) => (
          <tr
            key={o.id}
            className="border-b border-gray-200 last:border-b-0"
          >
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
    </TableShell>
  )
}

function KbTab({
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
    <TableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText="Geen kennisbanken gevonden."
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>Kennisbank</th>
          <th className={TH}>Organisatie</th>
          <th className={TH}>Type</th>
          <th className={TH}>Zichtbaarheid</th>
          <th className={TH}>Aangemaakt</th>
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
            className="border-b border-gray-200 last:border-b-0 cursor-pointer klai-hover"
          >
            <td className={TD}>
              <span className="font-medium">{kb.name}</span>
              <p className="text-xs text-gray-400 font-mono">{kb.slug}</p>
            </td>
            <td className={TD}>{kb.org_name}</td>
            <td className={TD}>
              <Badge variant="outline">
                {kb.owner_type === 'org' ? 'Organisatie' : 'Persoonlijk'}
              </Badge>
            </td>
            <td className={TD}>{kb.visibility}</td>
            <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
              {fmtDate(kb.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </TableShell>
  )
}

function BotsTab({
  search,
  fmtDate,
}: {
  search: string
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformBots(search)
  const rows = data ?? []
  return (
    <TableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText="Geen bots gevonden."
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>Bot</th>
          <th className={TH}>Organisatie</th>
          <th className={TH}>Kennisbanken</th>
          <th className={TH}>Aangemaakt</th>
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
            className="border-b border-gray-200 last:border-b-0 cursor-pointer klai-hover"
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
    </TableShell>
  )
}

function ChatErrorsTab({
  fmtDate,
}: {
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformChatErrors()
  const rows = data ?? []
  return (
    <TableShell
      loading={isLoading}
      empty={rows.length === 0}
      emptyText="Geen chat-errors gevonden."
    >
      <thead>
        <tr className="border-b border-gray-200">
          <th className={TH}>Type</th>
          <th className={TH}>Organisatie</th>
          <th className={TH}>Detail</th>
          <th className={TH}>Tijd</th>
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
              {e.detail ?? '—'}
            </td>
            <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
              {fmtDate(e.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </TableShell>
  )
}
