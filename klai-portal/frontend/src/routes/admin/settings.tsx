import { createFileRoute } from '@tanstack/react-router'
import { ExtensionsSettingsSection } from './_components/-ExtensionsSettingsSection'
import { LanguageSettingsSection } from './_components/-LanguageSettingsSection'
import { OrganizationSettingsSection } from './_components/-OrganizationSettingsSection'
import { SecuritySettingsSection } from './_components/-SecuritySettingsSection'
import { TelemetrySettingsSection } from './_components/-TelemetrySettingsSection'
import { useAdminSettings } from './-settings-hooks'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/settings')({
  component: AdminSettingsPage,
})

function AdminSettingsPage() {
  const { data: settings, isLoading, error } = useAdminSettings()

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6" data-help-id="admin-settings-general">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_settings_heading()}
        </h1>
        <p className="text-sm text-gray-400">
          {m.admin_settings_subtitle()}
        </p>
      </div>

      <LanguageSettingsSection settings={settings} isLoading={isLoading} error={error} />
      <SecuritySettingsSection settings={settings} isLoading={isLoading} error={error} />
      <OrganizationSettingsSection />
      <TelemetrySettingsSection settings={settings} isLoading={isLoading} error={error} />
      <ExtensionsSettingsSection />
    </div>
  )
}
