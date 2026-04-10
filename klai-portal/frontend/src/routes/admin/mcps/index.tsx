import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { Pencil } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { useMcpServers } from './_api'

export const Route = createFileRoute('/admin/mcps/')({
  component: McpsListPage,
})

function McpsListPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const token = auth.user?.access_token ?? ''

  const { data, isLoading, isError } = useMcpServers(token)

  const enabledServers = data?.servers.filter((s) => s.enabled) ?? []

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {m.admin_mcps_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.admin_mcps_subtitle()}
          </p>
        </div>
        <Button size="sm" onClick={() => navigate({ to: '/admin/mcps/new' })}>
          {m.admin_mcps_add_button()}
        </Button>
      </div>

      {isError ? (
        <p className="py-8 text-sm text-[var(--color-destructive)]">
          {m.admin_mcps_load_error()}
        </p>
      ) : isLoading ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_mcps_loading()}
        </p>
      ) : enabledServers.length === 0 ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_mcps_no_servers()}
        </p>
      ) : (
        <table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-48">
                {m.admin_mcps_col_name()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em]">
                {m.admin_mcps_col_description()}
              </th>
              <th className="py-3 text-right w-24" aria-label={m.admin_mcps_col_actions()} />
            </tr>
          </thead>
          <tbody>
            {enabledServers.map((server) => (
              <tr key={server.id} className="border-b border-[var(--color-border)] last:border-b-0">
                <td className="py-4 pr-4 align-top">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{server.display_name || server.id}</span>
                    {server.managed && (
                      <span className="rounded-full bg-[var(--color-muted)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-muted-foreground)]">
                        {m.admin_mcps_builtin()}
                      </span>
                    )}
                  </div>
                </td>
                <td className="py-4 pr-4 align-top text-[var(--color-muted-foreground)]">
                  {server.description}
                </td>
                <td className="py-4 align-top text-right w-24">
                  <div className="flex items-start justify-end gap-2 mt-px">
                    {!server.managed && (
                      <Tooltip label={m.admin_mcps_edit()}>
                        <button
                          onClick={() =>
                            navigate({
                              to: '/admin/mcps/$serverId',
                              params: { serverId: server.id },
                            })
                          }
                          aria-label={m.admin_mcps_edit()}
                          className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                      </Tooltip>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
