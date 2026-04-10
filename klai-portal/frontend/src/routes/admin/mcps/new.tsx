import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/mcps/new')({
  component: McpsNewPage,
})

interface McpServer {
  id: string
  display_name: string
  description: string
  enabled: boolean
  managed: boolean
  required_env_vars: string[]
  configured_env_vars: string[]
}

function McpsNewPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const token = auth.user?.access_token ?? ''

  const { data, isLoading, isError } = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: async () => apiFetch<{ servers: McpServer[] }>('/api/mcp-servers', token),
    enabled: !!token,
  })

  // Managed servers are always enabled and live in the main list — never in the picker.
  const availableServers = data?.servers.filter((s) => !s.enabled && !s.managed) ?? []

  function handleBack() {
    void navigate({ to: '/admin/mcps' })
  }

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {m.admin_integrations_new_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.admin_integrations_new_subtitle()}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_integrations_back()}
        </Button>
      </div>

      {isError && (
        <p className="text-sm text-[var(--color-destructive)]">
          {m.admin_integrations_save_error()}
        </p>
      )}

      {isLoading ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_integrations_loading()}
        </p>
      ) : availableServers.length === 0 ? (
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          {m.admin_integrations_new_empty()}
        </p>
      ) : (
        <table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-48">
                {m.admin_integrations_col_name()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em]">
                {m.admin_integrations_col_description()}
              </th>
              <th className="py-3 text-right w-24" aria-label={m.admin_integrations_col_actions()} />
            </tr>
          </thead>
          <tbody>
            {availableServers.map((server) => (
              <tr key={server.id} className="border-b border-[var(--color-border)] last:border-b-0">
                <td className="py-4 pr-4 align-top">
                  <span className="font-medium">{server.display_name || server.id}</span>
                </td>
                <td className="py-4 pr-4 align-top text-[var(--color-muted-foreground)]">
                  {server.description}
                </td>
                <td className="py-4 align-top text-right w-24">
                  <div className="flex items-start justify-end gap-2 mt-px">
                    <Tooltip label={m.admin_integrations_add_button()}>
                      <button
                        onClick={() =>
                          navigate({
                            to: '/admin/mcps/$serverId',
                            params: { serverId: server.id },
                          })
                        }
                        aria-label={m.admin_integrations_add_button()}
                        className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70"
                      >
                        <ArrowRight className="h-4 w-4" />
                      </button>
                    </Tooltip>
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
