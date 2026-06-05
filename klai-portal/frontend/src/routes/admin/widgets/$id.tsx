import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  ArrowLeft,
  Info,
  Shield,
  Palette,
  Code2,
  Plug,
  Activity,
  AlertTriangle,
  Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useWidget } from './-hooks'
import { DetailsTab } from './_components/tabs/DetailsTab'
import { KnowledgeBasesTab } from './_components/tabs/KnowledgeBasesTab'
import { AppearanceTab } from './_components/tabs/AppearanceTab'
import { EmbedTab } from './_components/tabs/EmbedTab'
import { IntegrationsTab } from './_components/tabs/IntegrationsTab'
import { ActivityTab } from './_components/tabs/ActivityTab'
import { DangerTab } from './_components/tabs/DangerTab'

type TabId = 'details' | 'kbs' | 'appearance' | 'embed' | 'integrations' | 'activity' | 'danger'

const VALID_TABS = new Set<TabId>([
  'details',
  'kbs',
  'appearance',
  'embed',
  'integrations',
  'activity',
  'danger',
])

type DetailSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/admin/widgets/$id')({
  validateSearch: (search: Record<string, unknown>): DetailSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: WidgetDetailPage,
})

function WidgetDetailPage() {
  const { id } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate()

  const { data: widget, isLoading, error, refetch } = useWidget(id)

  const showInternalIntegrations =
    typeof window !== 'undefined' && window.location.hostname === 'getklai.getklai.com'
  const requestedTab: TabId = search.tab ?? 'details'
  const activeTab: TabId =
    requestedTab === 'integrations' && !showInternalIntegrations
      ? 'details'
      : requestedTab

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="py-8 text-sm text-gray-400">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_widgets_loading()}
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

  if (!widget) return null

  const tabs: TabItem<TabId>[] = [
    { id: 'details', label: m.admin_shared_tab_general(), icon: Info },
    { id: 'kbs', label: m.admin_shared_wizard_step_kb_access(), icon: Shield },
    { id: 'appearance', label: m.admin_widgets_wizard_step_appearance(), icon: Palette },
    { id: 'embed', label: m.admin_widgets_wizard_step_embed(), icon: Code2 },
    ...(showInternalIntegrations
      ? [{ id: 'integrations' as const, label: m.admin_widgets_integrations_tab(), icon: Plug }]
      : []),
    { id: 'activity', label: m.admin_widgets_tab_activity(), icon: Activity },
    { id: 'danger', label: m.admin_shared_tab_danger(), icon: AlertTriangle },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/admin/widgets/$id',
      params: { id },
      search: { tab },
    })
  }

  return (
    <div className="mx-auto max-w-4xl px-6 pt-4 pb-10 space-y-8">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {widget.name}
          </h1>
          {widget.description && (
            <p className="text-sm text-gray-400 mt-1">
              {widget.description}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/widgets' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_widgets_back_to_list()}
        </Button>
      </div>

      <Tabs
        tabs={tabs}
        value={activeTab}
        onValueChange={setTab}
        className="overflow-x-auto"
      />

      {activeTab === 'details' && <DetailsTab widget={widget} />}
      {activeTab === 'kbs' && <KnowledgeBasesTab widget={widget} />}
      {activeTab === 'appearance' && <AppearanceTab widget={widget} />}
      {activeTab === 'embed' && <EmbedTab widget={widget} />}
      {activeTab === 'integrations' && showInternalIntegrations && (
        <IntegrationsTab widget={widget} />
      )}
      {activeTab === 'activity' && <ActivityTab widget={widget} />}
      {activeTab === 'danger' && <DangerTab widget={widget} />}
    </div>
  )
}
