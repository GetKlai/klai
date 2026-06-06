import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Activity, Info, Plug, Shield } from 'lucide-react'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { ExtensionsSettingsSection } from './_components/-ExtensionsSettingsSection'
import { LanguageSettingsSection } from './_components/-LanguageSettingsSection'
import { OrganizationSettingsSection } from './_components/-OrganizationSettingsSection'
import { SecuritySettingsSection } from './_components/-SecuritySettingsSection'
import { TelemetrySettingsSection } from './_components/-TelemetrySettingsSection'
import { useAdminSettings } from './-settings-hooks'
import * as m from '@/paraglide/messages'

type TabId = 'general' | 'security' | 'privacy' | 'features'

const VALID_TABS = new Set<TabId>(['general', 'security', 'privacy', 'features'])

type SettingsSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/admin/settings')({
  validateSearch: (search: Record<string, unknown>): SettingsSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: AdminSettingsPage,
})

function AdminSettingsPage() {
  const { data: settings, isLoading, error } = useAdminSettings()
  const search = Route.useSearch()
  const navigate = useNavigate()
  const activeTab: TabId = search.tab ?? 'general'

  const tabs: TabItem<TabId>[] = [
    { id: 'general', label: m.admin_settings_tab_general(), icon: Info },
    { id: 'security', label: m.admin_settings_tab_security(), icon: Shield },
    { id: 'privacy', label: m.admin_settings_tab_privacy(), icon: Activity },
    { id: 'features', label: m.admin_settings_tab_features(), icon: Plug },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/admin/settings',
      search: { tab },
    })
  }

  return (
    <div className="mx-auto max-w-4xl px-6 pt-4 pb-10 space-y-8" data-help-id="admin-settings-general">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.admin_settings_heading()}
        </h1>
      </div>

      <Tabs
        tabs={tabs}
        value={activeTab}
        onValueChange={setTab}
        className="overflow-x-auto"
      />

      {activeTab === 'general' && (
        <div className="space-y-6">
          <OrganizationSettingsSection settings={settings} isLoading={isLoading} error={error} />
          <LanguageSettingsSection settings={settings} isLoading={isLoading} error={error} />
        </div>
      )}
      {activeTab === 'security' && (
        <SecuritySettingsSection settings={settings} isLoading={isLoading} error={error} />
      )}
      {activeTab === 'privacy' && (
        <TelemetrySettingsSection settings={settings} isLoading={isLoading} error={error} />
      )}
      {activeTab === 'features' && <ExtensionsSettingsSection />}
    </div>
  )
}
