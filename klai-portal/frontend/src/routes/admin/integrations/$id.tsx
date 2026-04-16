import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft, Info, Shield, Settings, AlertTriangle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useIntegration } from './-hooks'
import { GeneralTab } from './_components/tabs/GeneralTab'
import { AccessTab } from './_components/tabs/AccessTab'
import { SettingsTab } from './_components/tabs/SettingsTab'
import { DangerTab } from './_components/tabs/DangerTab'

type IntegrationTab = 'general' | 'access' | 'settings' | 'danger'

const VALID_TABS = new Set<IntegrationTab>(['general', 'access', 'settings', 'danger'])

type DetailSearch = {
  tab?: IntegrationTab
}

export const Route = createFileRoute('/admin/integrations/$id')({
  validateSearch: (search: Record<string, unknown>): DetailSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as IntegrationTab)
      : undefined,
  }),
  component: IntegrationDetailPage,
})

function IntegrationDetailPage() {
  const { id } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate()

  const { data: integration, isLoading, error, refetch } = useIntegration(id)

  const activeTab: IntegrationTab = search.tab ?? 'general'

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_integrations_loading()}
        </p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 max-w-lg">
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  if (!integration) return null

  const isRevoked = integration.active === false
  const isWidget = integration.integration_type === 'widget'

  const tabs: {
    id: IntegrationTab
    label: string
    icon: React.ElementType
  }[] = [
    { id: 'general', label: m.admin_integrations_tab_general(), icon: Info },
    { id: 'access', label: m.admin_integrations_tab_access(), icon: Shield },
    { id: 'settings', label: m.admin_integrations_tab_settings(), icon: Settings },
    { id: 'danger', label: m.admin_integrations_tab_danger(), icon: AlertTriangle },
  ]

  function setTab(tab: IntegrationTab) {
    void navigate({
      to: '/admin/integrations/$id',
      params: { id },
      search: { tab },
    })
  }

  return (
    <div className="p-6 max-w-4xl space-y-8">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
              {integration.name}
            </h1>
            <Badge variant={isWidget ? 'accent' : 'default'}>
              {isWidget
                ? m.admin_integrations_type_badge_widget()
                : m.admin_integrations_type_badge_api()}
            </Badge>
            {isRevoked ? (
              <Badge variant="destructive">
                {m.admin_integrations_status_revoked()}
              </Badge>
            ) : (
              <Badge variant="success">
                {m.admin_integrations_status_active()}
              </Badge>
            )}
          </div>
          {integration.description && (
            <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
              {integration.description}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/integrations' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_integrations_back_to_list()}
        </Button>
      </div>

      {/* Tab bar */}
      <div className="border-b border-[var(--color-border)]">
        <nav className="-mb-px flex gap-6">
          {tabs.map(({ id: tabId, label, icon: TabIcon }) => {
            const isActive = tabId === activeTab
            return (
              <button
                key={tabId}
                type="button"
                onClick={() => setTab(tabId)}
                className={[
                  'flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 transition-colors',
                  isActive
                    ? 'border-[var(--color-accent)] text-[var(--color-foreground)]'
                    : 'border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
                ].join(' ')}
              >
                <TabIcon className="h-4 w-4" />
                {label}
              </button>
            )
          })}
        </nav>
      </div>

      {/* Active tab content */}
      {activeTab === 'general' && <GeneralTab integration={integration} />}
      {activeTab === 'access' && <AccessTab integration={integration} />}
      {activeTab === 'settings' && <SettingsTab integration={integration} />}
      {activeTab === 'danger' && <DangerTab integration={integration} />}
    </div>
  )
}
