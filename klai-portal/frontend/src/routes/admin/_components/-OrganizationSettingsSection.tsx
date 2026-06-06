import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import type { OrgSettings } from '../-settings-hooks'

interface OrganizationSettingsSectionProps {
  settings: OrgSettings | undefined
  isLoading: boolean
  error: unknown
}

export function OrganizationSettingsSection({
  settings,
  isLoading,
  error,
}: OrganizationSettingsSectionProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.admin_settings_org_title()}</CardTitle>
        <CardDescription>
          {m.admin_settings_org_description()}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
        ) : error ? (
          <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
        ) : (
          <dl className="grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-gray-400">
                {m.admin_settings_org_name_label()}
              </dt>
              <dd className="mt-1 text-sm font-medium text-gray-900">
                {settings?.name}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-gray-400">
                {m.admin_settings_org_domain_label()}
              </dt>
              <dd className="mt-1 text-sm font-medium text-gray-900">
                {settings?.primary_domain ?? m.admin_settings_org_domain_empty()}
              </dd>
            </div>
          </dl>
        )}
      </CardContent>
    </Card>
  )
}
