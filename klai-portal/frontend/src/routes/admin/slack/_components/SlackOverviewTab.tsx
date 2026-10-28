import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import type { SlackInstallation, SlackKey } from '../-types'

interface Props {
  installation: SlackInstallation
  slackKey: SlackKey
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

export function SlackOverviewTab({ installation, slackKey }: Props) {
  const rows: { label: string; value: string; mono?: boolean }[] = [
    { label: m.admin_slack_overview_workspace(), value: installation.team_name },
    { label: m.admin_slack_overview_team_id(), value: installation.team_id, mono: true },
    {
      label: m.admin_slack_overview_installed_by(),
      value: installation.installed_by_slack_user_id,
      mono: true,
    },
    {
      label: m.admin_slack_overview_installed_at(),
      value: formatDateTime(installation.installed_at),
    },
    { label: m.admin_slack_overview_key_prefix(), value: `${slackKey.key_prefix}...`, mono: true },
    {
      label: m.admin_slack_overview_last_used(),
      value: slackKey.last_used_at
        ? formatDateTime(slackKey.last_used_at)
        : m.admin_slack_overview_never_used(),
    },
    {
      label: m.admin_slack_overview_rate_limit(),
      value: m.admin_slack_overview_rate_limit_value({ rpm: slackKey.rate_limit_rpm }),
    },
  ]

  return (
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
  )
}
