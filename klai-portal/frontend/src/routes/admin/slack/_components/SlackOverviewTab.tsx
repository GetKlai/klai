import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import type { SlackInstallation } from '../-types'

interface Props {
  installation: SlackInstallation
}

function formatDateTime(iso: string): string {
  return datetime(getLocale(), iso, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function SlackOverviewTab({ installation }: Props) {
  const rows: { label: string; value: string; mono?: boolean }[] = [
    { label: m.admin_slack_overview_workspace(), value: installation.team_name },
    {
      label: m.admin_slack_overview_installed_at(),
      value: formatDateTime(installation.installed_at),
    },
    {
      label: m.admin_slack_overview_installed_by(),
      value: installation.installed_by_slack_user_id,
      mono: true,
    },
  ]
  const usage = [
    m.admin_slack_usage_dm(),
    m.admin_slack_usage_channel(),
    m.admin_slack_usage_kb(),
    m.admin_slack_usage_help(),
  ]

  return (
    <div className="space-y-8">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-[12rem_1fr]">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-sm text-gray-600">{row.label}</dt>
            <dd className={row.mono ? 'font-mono text-xs text-gray-900' : 'text-sm text-gray-900'}>
              {row.value}
            </dd>
          </div>
        ))}
      </dl>

      <div className="space-y-2">
        <h2 className="text-sm font-medium text-gray-900">{m.admin_slack_usage_title()}</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm text-gray-600">
          {usage.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </div>

      <p className="text-xs text-gray-600">
        {m.admin_slack_overview_team_id()}: <span className="font-mono">{installation.team_id}</span>
      </p>
    </div>
  )
}
