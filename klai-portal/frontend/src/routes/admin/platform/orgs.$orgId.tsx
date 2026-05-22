import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { usePlatformOrgDetail } from './-hooks'

export const Route = createFileRoute('/admin/platform/orgs/$orgId')({
  component: PlatformOrgDetailPage,
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
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Gebruikers" value={data.org.user_count} />
            <Stat label="Bots" value={data.org.bot_count} />
            <Stat label="Seats" value={data.org.seats} />
            <Stat label="Billing" value={data.org.billing_status} />
          </div>

          {/* Users */}
          <section>
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-gray-400 mb-3">
              Gebruikers ({data.users.length})
            </h2>
            {data.users.length === 0 ? (
              <p className="text-sm text-gray-400">Geen gebruikers.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-t border-b border-gray-200">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className={TH}>Gebruiker</th>
                      <th className={TH}>Rol</th>
                      <th className={TH}>Status</th>
                      <th className={TH}>Aangemaakt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.users.map((u) => (
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
                          <Badge variant="outline">{u.role}</Badge>
                        </td>
                        <td className={TD}>{u.status}</td>
                        <td
                          className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}
                        >
                          {fmtDate(u.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

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
        </>
      )}
    </div>
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
