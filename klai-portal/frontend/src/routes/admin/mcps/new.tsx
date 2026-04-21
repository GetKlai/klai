import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { useMcpServers } from './_api'

export const Route = createFileRoute('/admin/mcps/new')({
  component: McpsNewPage,
})

function McpsNewPage() {
  const navigate = useNavigate()
  const { data, isLoading, isError } = useMcpServers()

  // Managed servers are always enabled and live in the main list — never in the picker.
  const availableServers = data?.servers.filter((s) => !s.enabled && !s.managed) ?? []

  function handleBack() {
    void navigate({ to: '/admin/mcps' })
  }

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-gray-900">
            {m.admin_mcps_new_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {m.admin_mcps_new_subtitle()}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_mcps_back()}
        </Button>
      </div>

      {isError ? (
        <p className="py-8 text-sm text-[var(--color-destructive)]">
          {m.admin_mcps_load_error()}
        </p>
      ) : isLoading ? (
        <p className="py-8 text-sm text-gray-400">
          {m.admin_mcps_loading()}
        </p>
      ) : availableServers.length === 0 ? (
        <p className="py-8 text-sm text-gray-400">
          {m.admin_mcps_new_empty()}
        </p>
      ) : (
        <table className="w-full text-sm table-fixed border-t border-b border-gray-200">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-48">
                {m.admin_mcps_col_name()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em]">
                {m.admin_mcps_col_description()}
              </th>
              <th className="py-3 text-right w-24" aria-label={m.admin_mcps_col_actions()} />
            </tr>
          </thead>
          <tbody>
            {availableServers.map((server) => (
              <tr key={server.id} className="border-b border-gray-200 last:border-b-0">
                <td className="py-4 pr-4 align-top">
                  <span className="font-medium">{server.display_name || server.id}</span>
                </td>
                <td className="py-4 pr-4 align-top text-gray-400">
                  {server.description}
                </td>
                <td className="py-4 align-top text-right w-24">
                  <div className="flex items-start justify-end gap-2 mt-px">
                    <Tooltip label={m.admin_mcps_add_button()}>
                      <button
                        onClick={() =>
                          navigate({
                            to: '/admin/mcps/$serverId',
                            params: { serverId: server.id },
                          })
                        }
                        aria-label={m.admin_mcps_add_button()}
                        className="inline-flex items-center justify-center text-gray-500 transition-opacity hover:opacity-70"
                      >
                        <Plus className="h-4 w-4" />
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
